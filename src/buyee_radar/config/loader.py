"""Configuration : YAML pour les réglages, environnement pour les secrets.

La séparation est stricte et vérifiée par un test : **aucun secret ne peut
atterrir dans `radar.yaml`**. Le fichier YAML est fait pour être partagé,
versionné, envoyé en capture d'écran quand on demande de l'aide. Un jeton
Telegram ou une URL de webhook qui s'y trouve finit tôt ou tard publié.

Les secrets viennent donc uniquement de l'environnement, ou d'un `.env`
git-ignoré :

    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID
    DISCORD_WEBHOOK_URL
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

#: Volontairement PAS « config.yaml » : la génération précédente
#: (`snipe`) utilise déjà ce nom-là avec un schéma différent. Deux
#: outils qui se disputent le même fichier, ça donne un des deux qui
#: démarre en silence avec ses valeurs par défaut — panne invisible.
DEFAULT_PATH = Path("radar.yaml")
#: Clés jamais sérialisées, quoi qu'il arrive.
SECRET_FIELDS = frozenset({
    "telegram_token", "telegram_chat_id", "discord_webhook", "token", "webhook",
})


@dataclass
class SourceSettings:
    """Une marketplace Buyee. `selectors` vient de `radar calibrate`."""

    enabled: bool = False
    max_concurrent: int = 4
    connect_timeout: float = 5.0
    read_timeout: float = 10.0
    total_timeout: float = 15.0
    max_retries: int = 2
    page_size: int = 60
    #: Surcharge le gabarit d'URL si Buyee change de forme.
    search_url: str = ""
    #: Sélecteurs CSS de la page de résultats. Vides = source en attente de
    #: calibration, et le dashboard l'affiche comme telle.
    selectors: dict[str, str] = field(default_factory=dict)
    extra_params: dict[str, str] = field(default_factory=dict)


@dataclass
class ScannerSettings:
    #: Cadences par priorité, en secondes.
    interval_high: float = 2.0
    interval_medium: float = 5.0
    interval_low: float = 20.0
    #: Requêtes/seconde, toutes sources confondues. Ajouter des mots-clés
    #: répartit ce budget, il ne grandit pas tout seul.
    budget_per_second: float = 8.0
    jitter: float = 0.15
    #: Pages supplémentaires demandées quand un trou est détecté. Le
    #: monitoring temps réel vit sur la page 1 ; on ne balaie jamais 10 pages
    #: par principe.
    max_catchup_pages: int = 3
    #: Premier passage silencieux : sans lui, tout ce qui est déjà en ligne
    #: partirait en notification au démarrage.
    warmup: bool = True
    status_every: float = 60.0
    #: Requêtes simultanées, toutes sources confondues. Borne le nombre de
    #: connexions ouvertes en même temps ; le débit, lui, est gouverné par
    #: `budget_per_second`.
    max_concurrency: int = 20

    @property
    def intervals(self) -> dict[str, float]:
        return {
            "high": self.interval_high,
            "medium": self.interval_medium,
            "low": self.interval_low,
        }


@dataclass
class FilterSettings:
    min_price: int | None = None
    max_price: int | None = None
    exclude: list[str] = field(default_factory=list)
    exclude_regex: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    max_age_seconds: int = 900


@dataclass
class NotificationSettings:
    telegram_enabled: bool = True
    telegram_photo: bool = True
    telegram_silent: bool = False
    discord_enabled: bool = False
    console: bool = True
    max_queue: int = 500
    max_retries: int = 3


@dataclass
class StorageSettings:
    database: str = "data/radar.db"
    retention_days: int = 30
    dedup_capacity: int = 200_000
    flush_every: float = 5.0


@dataclass
class ScoringSettings:
    """Seuils de notification par palier de score (§24)."""

    notify_min_score: int = 0        # 0 = tout arrive au dashboard
    telegram_min_score: int = 70
    discord_min_score: int = 50
    bargain_price: int = 6000
    expensive_price: int = 60000


@dataclass
class CurrencySettings:
    target: str = "EUR"
    #: Taux imposé. Le renseigner supprime tout appel réseau.
    fixed_rate: float | None = None
    ttl_seconds: float = 21600.0
    allow_network: bool = False


@dataclass
class BuyeeSettings:
    enabled: bool = True
    affiliate_id: str = ""
    templates: dict[str, str] = field(default_factory=dict)


@dataclass
class KeywordSettings:
    name: str
    search: list[str] = field(default_factory=list)
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    include_regex: list[str] = field(default_factory=list)
    exclude_regex: list[str] = field(default_factory=list)
    priority: str = "medium"
    min_price: int | None = None
    max_price: int | None = None
    sources: list[str] = field(default_factory=list)
    enabled: bool = True


@dataclass
class Settings:
    keywords: list[KeywordSettings] = field(default_factory=list)
    sources: dict[str, SourceSettings] = field(default_factory=dict)
    scanner: ScannerSettings = field(default_factory=ScannerSettings)
    filters: FilterSettings = field(default_factory=FilterSettings)
    notifications: NotificationSettings = field(default_factory=NotificationSettings)
    storage: StorageSettings = field(default_factory=StorageSettings)
    buyee: BuyeeSettings = field(default_factory=BuyeeSettings)
    scoring: ScoringSettings = field(default_factory=ScoringSettings)
    currency: CurrencySettings = field(default_factory=CurrencySettings)
    api_host: str = "127.0.0.1"
    api_port: int = 8899
    log_level: str = "INFO"

    # ── Secrets : jamais sérialisés ───────────────────────────────────────
    telegram_token: str = field(default="", repr=False)
    telegram_chat_id: str = field(default="", repr=False)
    discord_webhook: str = field(default="", repr=False)

    path: Path | None = field(default=None, repr=False, compare=False)

    # ── Chargement ────────────────────────────────────────────────────────
    @classmethod
    def load(cls, path: str | Path | None = None) -> "Settings":
        path = Path(path) if path else DEFAULT_PATH
        raw: dict[str, Any] = {}
        if path.exists():
            raw = yaml.safe_load(path.read_text("utf-8")) or {}
        settings = cls.from_dict(raw)
        settings.path = path
        settings.apply_env()
        return settings

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Settings":
        def section(name: str, klass):
            data = raw.get(name) or {}
            known = set(klass.__dataclass_fields__)
            return klass(**{k: v for k, v in data.items() if k in known})

        keywords = []
        for item in raw.get("keywords") or []:
            if isinstance(item, str):
                keywords.append(KeywordSettings(name=item))
            elif isinstance(item, dict) and item.get("name"):
                known = set(KeywordSettings.__dataclass_fields__)
                keywords.append(
                    KeywordSettings(**{k: v for k, v in item.items() if k in known})
                )

        sources = {}
        for name, data in (raw.get("sources") or {}).items():
            known = set(SourceSettings.__dataclass_fields__)
            sources[name] = SourceSettings(
                **{k: v for k, v in (data or {}).items() if k in known}
            )

        return cls(
            keywords=keywords,
            sources=sources,
            scanner=section("scanner", ScannerSettings),
            filters=section("filters", FilterSettings),
            notifications=section("notifications", NotificationSettings),
            storage=section("storage", StorageSettings),
            buyee=section("buyee", BuyeeSettings),
            scoring=section("scoring", ScoringSettings),
            currency=section("currency", CurrencySettings),
            api_host=str(raw.get("api_host") or "127.0.0.1"),
            api_port=int(raw.get("api_port") or 8899),
            log_level=str(raw.get("log_level") or "INFO"),
        )

    def apply_env(self) -> None:
        """Charge les secrets. C'est leur SEULE provenance."""
        load_dotenv()
        self.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.discord_webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

        if value := os.getenv("RADAR_LOG_LEVEL"):
            self.log_level = value.strip().upper()
        if value := os.getenv("RADAR_BUDGET"):
            try:
                self.scanner.budget_per_second = float(value)
            except ValueError:
                log.warning("RADAR_BUDGET invalide : %r", value)
        if os.getenv("TELEGRAM_ENABLED", "").lower() in ("0", "false", "no"):
            self.notifications.telegram_enabled = False

    # ── Sérialisation ─────────────────────────────────────────────────────
    def to_yaml_dict(self) -> dict[str, Any]:
        """Dict prêt à écrire — garanti sans secret."""
        data = {
            "log_level": self.log_level,
            "keywords": [_clean(asdict(k)) for k in self.keywords],
            "sources": {n: _clean(asdict(s)) for n, s in self.sources.items()},
            "scanner": asdict(self.scanner),
            "filters": asdict(self.filters),
            "notifications": asdict(self.notifications),
            "storage": asdict(self.storage),
            "buyee": asdict(self.buyee),
            "scoring": asdict(self.scoring),
            "currency": asdict(self.currency),
            "api_host": self.api_host,
            "api_port": self.api_port,
        }
        _assert_no_secrets(data)
        return data

    def save(self, path: str | Path | None = None) -> Path:
        path = Path(path) if path else (self.path or DEFAULT_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(
                self.to_yaml_dict(), allow_unicode=True, sort_keys=False, width=100
            ),
            encoding="utf-8",
        )
        self.path = path
        return path


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    """Retire les valeurs vides pour garder le YAML lisible."""
    return {
        k: v for k, v in data.items()
        if v not in (None, [], {}, "") or isinstance(v, bool)
    }


def _assert_no_secrets(data: Any, path: str = "") -> None:
    """Garde-fou : lève si un secret a réussi à se glisser dans le YAML.

    Une assertion plutôt qu'un filtrage silencieux : si ce cas se produit,
    c'est une erreur de programmation qu'il faut voir, pas masquer.
    """
    if isinstance(data, dict):
        for key, value in data.items():
            if key in SECRET_FIELDS:
                raise AssertionError(
                    f"secret « {key} » sur le point d'être écrit dans "
                    f"radar.yaml (chemin {path}/{key})"
                )
            _assert_no_secrets(value, f"{path}/{key}")
    elif isinstance(data, list):
        for index, item in enumerate(data):
            _assert_no_secrets(item, f"{path}[{index}]")


def load_dotenv(path: str | Path = ".env") -> None:
    """Charge un `.env` minimal, sans dépendance externe.

    L'environnement réel gagne toujours sur le fichier : un `export` en
    console doit pouvoir surcharger le `.env` pour un test ponctuel.
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
