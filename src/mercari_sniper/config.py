"""Configuration : YAML + variables d'environnement, avec import de la v1.

Règle de sécurité : aucun secret n'est écrit dans le YAML. Le webhook Discord
et les éventuels tokens sont lus depuis l'environnement (ou un fichier .env),
qui est git-ignoré.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("config.yaml")


# ── Sous-sections ─────────────────────────────────────────────────────────────
@dataclass
class SourceConfig:
    """Une requête envoyée à Mercari.

    Une source « large » (ex. ナイキ) ramène 120 annonces récentes d'un coup ;
    le matching local se charge d'y retrouver les 60 keywords précis. C'est
    l'inverse du bot v1, qui envoyait une requête par keyword.
    """

    query: str
    page_size: int = 120
    interval: float | None = None      # None => poll.interval
    weight: float = 1.0                # >1 = interrogée plus souvent
    min_price: int | None = None
    max_price: int | None = None
    exclude_keyword: str = ""
    enabled: bool = True
    # True quand la source a été créée automatiquement pour couvrir un
    # keyword : elle disparaîtra si ce keyword est retiré. Les sources
    # écrites à la main dans le YAML ne sont jamais supprimées.
    auto: bool = False


@dataclass
class PollConfig:
    interval: float = 2.0              # cible par source (secondes)
    min_interval: float = 1.0
    max_interval: float = 60.0
    jitter: float = 0.15               # ±15 % pour désynchroniser les sources
    global_rate_limit: float = 5.0     # requêtes/seconde, toutes sources
    burst_on_hit: bool = True          # re-poller aussitôt après une trouvaille
    warmup: bool = True                # 1er tour = mémorisation silencieuse
    max_catchup_pages: int = 3         # pages remontées quand on détecte un trou
    adaptive: bool = True              # ajuste l'intervalle sur le débit réel


@dataclass
class FiltersConfig:
    min_price: int | None = None
    max_price: int | None = None
    exclude_words: list[str] = field(default_factory=list)
    max_age_seconds: int = 900         # ignore ce qui est plus vieux que ça


@dataclass
class NotifyConfig:
    discord_enabled: bool = True
    discord_rate_limit: float = 2.0    # messages/seconde vers le webhook
    discord_max_queue: int = 500
    console: bool = True
    min_rarity: str = "PREMIUM"        # PREMIUM | RARE | ULTRA RARE


@dataclass
class BuyeeConfig:
    """Liens vers le proxy d'achat Buyee.

    Les gabarits sont configurables parce que le format d'URL de Buyee n'a
    pas pu être vérifié en ligne : si le défaut est faux, corrige ici plutôt
    que dans le code.
    """

    enabled: bool = True
    item_template: str = "https://buyee.jp/item/mercari/item/{id}"
    shop_template: str = "https://buyee.jp/item/mercari/shops/{id}"
    affiliate_id: str = ""


@dataclass
class ServerConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8420
    open_browser: bool = True
    max_feed: int = 300                # annonces gardées côté dashboard


@dataclass
class StorageConfig:
    database: str = "data/sniper.db"
    retention_days: int = 30
    seen_cache_size: int = 100_000
    # Tampon d'annonces brutes : permet à un keyword ajouté après coup de
    # retrouver ce qui a déjà été scanné.
    recent_buffer_seconds: int = 1800
    recent_buffer_size: int = 6000


@dataclass
class Config:
    keywords: list[str] = field(default_factory=list)
    sources: list[SourceConfig] = field(default_factory=list)
    poll: PollConfig = field(default_factory=PollConfig)
    filters: FiltersConfig = field(default_factory=FiltersConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    buyee: BuyeeConfig = field(default_factory=BuyeeConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    backend: str = "mercari"           # mercari | simulator
    log_level: str = "INFO"

    # Secrets — jamais sérialisés dans le YAML.
    discord_webhook: str = field(default="", repr=False)

    # Chemin d'origine : permet à save() de réécrire au bon endroit sans
    # que l'appelant ait à le retenir.
    path: Path | None = field(default=None, repr=False, compare=False)

    # ── Chargement ────────────────────────────────────────────────────────
    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        path = Path(path) if path else DEFAULT_CONFIG_PATH
        raw: dict[str, Any] = {}
        if path.exists():
            raw = yaml.safe_load(path.read_text("utf-8")) or {}
            log.debug("config chargée depuis %s", path)

        config = cls.from_dict(raw)
        config.path = path
        config.apply_env()
        config.ensure_sources()
        return config

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Config":
        def section(name: str, klass):
            data = raw.get(name) or {}
            known = {f for f in klass.__dataclass_fields__}
            return klass(**{k: v for k, v in data.items() if k in known})

        sources = [
            SourceConfig(**{
                k: v for k, v in item.items()
                if k in SourceConfig.__dataclass_fields__
            })
            for item in (raw.get("sources") or [])
            if isinstance(item, dict) and item.get("query")
        ]

        return cls(
            keywords=[str(k) for k in (raw.get("keywords") or []) if str(k).strip()],
            sources=sources,
            poll=section("poll", PollConfig),
            filters=section("filters", FiltersConfig),
            notify=section("notify", NotifyConfig),
            buyee=section("buyee", BuyeeConfig),
            server=section("server", ServerConfig),
            storage=section("storage", StorageConfig),
            backend=str(raw.get("backend") or "mercari"),
            log_level=str(raw.get("log_level") or "INFO"),
        )

    def apply_env(self) -> None:
        """Surcharge par l'environnement. Les secrets ne viennent QUE d'ici."""
        load_dotenv()

        self.discord_webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

        if value := os.getenv("SNIPER_BACKEND"):
            self.backend = value.strip()
        if value := os.getenv("SNIPER_PORT"):
            with_int(value, lambda v: setattr(self.server, "port", v))
        if value := os.getenv("SNIPER_HOST"):
            self.server.host = value.strip()
        if value := os.getenv("SNIPER_INTERVAL"):
            with_float(value, lambda v: setattr(self.poll, "interval", v))
        if value := os.getenv("SNIPER_RATE_LIMIT"):
            with_float(value, lambda v: setattr(self.poll, "global_rate_limit", v))
        if value := os.getenv("SNIPER_LOG_LEVEL"):
            self.log_level = value.strip().upper()

    def ensure_sources(self) -> None:
        """Sans sources explicites, dérive des requêtes larges des keywords.

        On regroupe par premier mot : « ナイキ トレイル », « ナイキ ベスト »… ont
        tous la racine « ナイキ », donc une seule requête large les couvre.
        """
        if self.sources or not self.keywords:
            return

        roots: dict[str, int] = {}
        for keyword in self.keywords:
            root = keyword.strip().split()[0] if keyword.strip() else ""
            if root:
                roots[root] = roots.get(root, 0) + 1

        # Une racine ne mérite sa requête large que si elle couvre >1 keyword ;
        # sinon on interroge le keyword complet, plus sélectif.
        self.sources = [
            SourceConfig(query=root, weight=1.0 + min(count, 10) / 10, auto=True)
            for root, count in sorted(roots.items(), key=lambda kv: -kv[1])
            if count > 1
        ]
        singles = [
            keyword for keyword in self.keywords
            if roots.get(keyword.strip().split()[0] if keyword.strip() else "", 0) <= 1
        ]
        self.sources.extend(
            SourceConfig(query=kw, page_size=60, auto=True) for kw in singles
        )
        log.info(
            "%d sources dérivées de %d keywords", len(self.sources), len(self.keywords)
        )

    # ── Sérialisation ─────────────────────────────────────────────────────
    def to_yaml_dict(self) -> dict[str, Any]:
        """Dict prêt à écrire — sans aucun secret."""
        return {
            "backend": self.backend,
            "log_level": self.log_level,
            "keywords": self.keywords,
            "sources": [asdict(s) for s in self.sources],
            "poll": asdict(self.poll),
            "filters": asdict(self.filters),
            "notify": asdict(self.notify),
            "buyee": asdict(self.buyee),
            "server": asdict(self.server),
            "storage": asdict(self.storage),
        }

    def save(self, path: str | Path | None = None) -> Path:
        path = Path(path) if path else (self.path or DEFAULT_CONFIG_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(
                self.to_yaml_dict(),
                allow_unicode=True,
                sort_keys=False,
                width=100,
            ),
            encoding="utf-8",
        )
        self.path = path
        return path


# ── Utilitaires ───────────────────────────────────────────────────────────────
def with_int(value: str, setter) -> None:
    try:
        setter(int(value))
    except (TypeError, ValueError):
        log.warning("valeur entière invalide ignorée: %r", value)


def with_float(value: str, setter) -> None:
    try:
        setter(float(value))
    except (TypeError, ValueError):
        log.warning("valeur décimale invalide ignorée: %r", value)


def load_dotenv(path: str | Path = ".env") -> None:
    """Charge un .env minimal sans dépendance externe.

    Les variables déjà présentes dans l'environnement gagnent, pour qu'un
    export shell puisse toujours surcharger le fichier.
    """
    path = Path(path)
    if not path.exists():
        return
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# ── Import depuis le bot v1 ───────────────────────────────────────────────────
def import_legacy(
    directory: str | Path = ".",
) -> tuple[list[str], dict[str, Any], str]:
    """Lit `keywords.json` / `mercari_config.json` du bot d'origine.

    Renvoie (keywords, réglages, webhook). Le webhook est renvoyé à part :
    l'appelant doit l'écrire dans .env, jamais dans le YAML.
    """
    directory = Path(directory)
    keywords: list[str] = []
    settings: dict[str, Any] = {}
    webhook = ""

    keywords_file = directory / "keywords.json"
    if keywords_file.exists():
        try:
            data = json.loads(keywords_file.read_text("utf-8"))
            keywords = [str(k) for k in data.get("keywords", []) if str(k).strip()]
            log.info("v1: %d keywords importés", len(keywords))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("keywords.json illisible: %s", exc)

    config_file = directory / "mercari_config.json"
    if config_file.exists():
        try:
            data = json.loads(config_file.read_text("utf-8"))
            webhook = str(data.get("webhook_url") or "").strip()
            settings = {
                "min_price": data.get("min_price"),
                "max_price": data.get("max_price"),
                "warmup": bool(data.get("warmup_first_run", True)),
            }
            log.info("v1: réglages importés depuis %s", config_file.name)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("mercari_config.json illisible: %s", exc)

    return keywords, settings, webhook


def import_legacy_seen(directory: str | Path = ".") -> list[str]:
    """Récupère les ids déjà vus par le bot v1 (évite un re-spam au 1er lancement)."""
    path = Path(directory) / "seen_items.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text("utf-8"))
        return list(data.keys()) if isinstance(data, dict) else list(data)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("seen_items.json illisible: %s", exc)
        return []
