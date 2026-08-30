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

from . import noise

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
    categories: list[int] = field(default_factory=list)
    enabled: bool = True
    # True quand la source a été créée automatiquement pour couvrir un
    # keyword : elle disparaîtra si ce keyword est retiré. Les sources
    # écrites à la main dans le YAML ne sont jamais supprimées.
    auto: bool = False


@dataclass
class PollConfig:
    interval: float = 2.0              # cible par source (secondes)
    min_interval: float = 1.0
    # Plafond atteint uniquement quand le budget de requêtes est saturé.
    # Une source calme N'EST PAS ralentie : c'est précisément sur elle que
    # la réactivité compte, l'annonce rare n'arrivant qu'une fois par heure.
    max_interval: float = 60.0
    jitter: float = 0.15               # ±15 % pour désynchroniser les sources
    global_rate_limit: float = 8.0     # requêtes/seconde, toutes sources
    burst_on_hit: bool = True          # re-poller aussitôt après une trouvaille
    warmup: bool = True                # 1er tour = mémorisation silencieuse
    max_catchup_pages: int = 5         # pages remontées quand on détecte un trou
    adaptive: bool = True              # resserre les sources qui débordent
    # Une requête large qui ne produit presque aucune trouvaille gaspille le
    # budget. En dessous de ce rendement (trouvailles / annonces vues), et
    # passé `split_min_items` annonces observées, elle est remplacée par des
    # requêtes précises, une par keyword couvert.
    auto_split: bool = True
    split_min_yield: float = 0.005
    split_min_items: int = 400


@dataclass
class FiltersConfig:
    min_price: int | None = None
    max_price: int | None = None
    exclude_words: list[str] = field(default_factory=list)
    max_age_seconds: int = 900         # ignore ce qui est plus vieux que ça

    # Familles de bruit écartées d'office (voir noise.py). Un keyword de
    # marque seule — « dior » — ramène sinon surtout du parfum et du
    # maquillage, qui sont parmi les articles les plus publiés du site.
    noise_groups: list[str] = field(
        default_factory=lambda: list(noise.DEFAULT_GROUPS)
    )
    # Restreint la recherche à des catégories Mercari (ids racines : 1
    # レディース, 2 メンズ, 3 ベビー・キッズ, 4 インテリア, 5 本・音楽・ゲーム,
    # 6 おもちゃ・ホビー, 7 コスメ・香水・美容, 8 家電・スマホ, 9 スポーツ,
    # 10 ハンドメイド, 11 チケット, 12 自動車, 13 その他). Vide = toutes.
    include_categories: list[int] = field(default_factory=list)

    def noise_terms(self) -> list[str]:
        return noise.terms_for(self.noise_groups)

    def all_exclude_terms(self) -> list[str]:
        """Exclusions de l'utilisateur d'abord, bruit intégré ensuite.

        L'ordre compte : la chaîne `excludeKeyword` envoyée à Mercari est
        tronquée pour rester d'une taille raisonnable, et c'est la fin de la
        liste qui saute. Or ce que l'utilisateur a saisi lui-même est ce à
        quoi il tient le plus — il doit passer avant le vocabulaire générique.
        Le filtre local, lui, applique tout et se moque de l'ordre.
        """
        terms: list[str] = []
        known: set[str] = set()
        for word in list(self.exclude_words) + self.noise_terms():
            word = str(word).strip()
            if word and word not in known:
                known.add(word)
                terms.append(word)
        return terms


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
        if config.migrate_sources():
            config.save(path)
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
        """Sans sources explicites, interroge chaque keyword tel quel.

        Le choix inverse — regrouper « ナイキ トレイル » et « ナイキ ベスト »
        derrière une seule requête large « ナイキ » — paraît économe : une
        requête au lieu de deux. En pratique il ruine le rendement.

        Mercari trie par date et ne renvoie que les 120 annonces les plus
        récentes. Sur « ナイキ », ces 120 annonces couvrent quelques secondes
        et sont à 99 % hors sujet : le budget de requêtes part presque
        entièrement dans des articles qui ne matcheront jamais, et la page
        déborde en permanence, donc on rate quand même des annonces.

        Une requête précise, elle, est filtrée par Mercari : ses 120 places
        sont toutes pertinentes et couvrent des heures. On interroge donc le
        keyword complet, et l'utilisateur reste libre d'ajouter des requêtes
        larges à la main dans le YAML — elles ne sont jamais supprimées.
        """
        if self.sources or not self.keywords:
            return

        seen: set[str] = set()
        for keyword in self.keywords:
            query = " ".join(keyword.split())
            if not query or query in seen:
                continue
            seen.add(query)
            self.sources.append(
                SourceConfig(query=query, page_size=60, auto=True)
            )
        log.info(
            "%d sources dérivées de %d keywords", len(self.sources), len(self.keywords)
        )

    def migrate_sources(self) -> list[str]:
        """Remplace les requêtes élargies écrites par les versions ≤ 2.2.

        Ces versions dérivaient une requête du PREMIER MOT du keyword :
        « ナイキ トレイル » interrogeait « ナイキ ». Le rendement s'effondrait
        (voir `ensure_sources`). Le moteur finit par s'en apercevoir tout
        seul, mais il lui faut quelques centaines d'annonces observées ; on
        n'attend pas pour un cas où la réponse est déjà connue.

        Ne touche QUE les sources marquées `auto` : celles-là ont été
        générées par le bot. Une requête large écrite à la main dans le YAML
        est un choix de l'utilisateur et n'est jamais retirée.

        Renvoie les requêtes remplacées, pour que l'appelant puisse le dire.
        """
        from .matching import covers, normalize

        existing = {source.query for source in self.sources}
        replaced: list[str] = []
        kept: list[SourceConfig] = []

        for source in self.sources:
            if not source.auto:
                kept.append(source)
                continue

            covered = [
                keyword for keyword in self.keywords
                if covers(source.query, keyword)
                and normalize(keyword) != normalize(source.query)
            ]
            if not covered:
                kept.append(source)          # déjà précise, ou orpheline
                continue

            replaced.append(source.query)
            existing.discard(source.query)
            for keyword in covered:
                if keyword not in existing:
                    existing.add(keyword)
                    kept.append(
                        SourceConfig(query=keyword, page_size=60, auto=True)
                    )

        if replaced:
            self.sources = kept
            log.warning(
                "%d requête(s) élargie(s) remplacée(s) par des requêtes "
                "précises : %s — l'ancienne façon de faire gaspillait "
                "l'essentiel du budget de scan",
                len(replaced),
                ", ".join(replaced),
            )
        return replaced

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
