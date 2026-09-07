"""Configuration : YAML pour les réglages, environnement pour les secrets.

La séparation est stricte et vérifiée par un test : **aucun secret ne peut
atterrir dans `radar.yaml`**. Le fichier YAML est fait pour être partagé,
versionné, envoyé en capture d'écran quand on demande de l'aide. Un jeton
Telegram ou une URL de webhook qui s'y trouve finit tôt ou tard publié.

Les secrets viennent donc uniquement de l'environnement, ou d'un `.env`
git-ignoré :

    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID
    TELEGRAM_TOPIC_ID     (facultatif : un sujet précis d'un groupe Forum)
    DISCORD_WEBHOOK_URL
    DISCORD_THREAD_ID     (facultatif : un fil précis du salon du webhook)
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
    "telegram_token", "telegram_chat_id", "telegram_topic_id",
    "discord_webhook", "discord_thread_id", "token", "webhook",
})


@dataclass
class MercariSettings:
    """La source. Une seule, et elle n'a pas besoin d'être « activée »."""

    #: Requêtes simultanées vers l'API. Au-delà de ~8, Mercari répond 429
    #: sans que le débit utile augmente.
    max_concurrent: int = 6
    connect_timeout: float = 5.0
    read_timeout: float = 10.0
    total_timeout: float = 15.0
    max_retries: int = 2
    #: Annonces demandées par requête. 120 est le plafond de l'API.
    page_size: int = 60
    #: Proxy HTTP, si le réseau local en impose un.
    proxy: str = ""


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
    #: « clean » : le nom, le prix en euros, le lien. Rien d'autre — c'est
    #: ce qui se lit sur un téléphone. « detailed » ajoute mot-clé, score
    #: et latence.
    telegram_style: str = "clean"
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
    enabled: bool = True


def _coerce(value: Any, field, where: str) -> Any:
    """Convertit une valeur du YAML vers le type attendu par le champ.

    Un `radar.yaml` s'édite à la main. Une virgule oubliée, un nombre entre
    guillemets, une liste là où on attend un dictionnaire : ça arrive, et ça
    ne doit JAMAIS faire tomber le bot au démarrage. On convertit ce qui est
    convertible, on retombe sur la valeur par défaut sinon — en nommant la
    clé fautive, pour que ce soit réparable.
    """
    import dataclasses
    import typing

    annotation = field.type
    if isinstance(annotation, str):
        # `from __future__ import annotations` : les types sont des chaînes.
        annotation = {
            "bool": bool, "int": int, "float": float, "str": str,
            "list[str]": list, "dict[str, str]": dict,
            "int | None": int, "float | None": float,
        }.get(annotation.strip(), None)
    if annotation is None:
        return value

    origin = typing.get_origin(annotation) or annotation

    if origin in (list, tuple):
        if isinstance(value, (list, tuple)):
            return list(value)
        if isinstance(value, str):
            # Une chaîne seule là où on attend une liste : intention claire.
            return [value]
        raise TypeError("une liste est attendue")
    if origin is dict:
        if isinstance(value, dict):
            return {str(k): v for k, v in value.items()}
        raise TypeError("un dictionnaire est attendu")
    if origin is bool:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("1", "true", "yes", "oui", "on"):
            return True
        if text in ("0", "false", "no", "non", "off"):
            return False
        raise TypeError("oui/non attendu")
    if origin in (int, float):
        if value is None:
            return None
        return origin(str(value).replace(" ", "").replace(",", "."))
    if origin is str:
        return "" if value is None else str(value)
    return value


def build(klass, data: Any, *, where: str, required: dict | None = None):
    """Instancie une section de configuration sans jamais lever."""
    if data is None:
        data = {}
    if not isinstance(data, dict):
        log.error("« %s » doit être un dictionnaire — section ignorée", where)
        data = {}

    fields = klass.__dataclass_fields__
    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        field = fields.get(str(key))
        if field is None:
            log.warning("« %s.%s » inconnu — ignoré", where, key)
            continue
        try:
            kwargs[str(key)] = _coerce(value, field, where)
        except (TypeError, ValueError) as exc:
            log.error(
                "« %s.%s » = %r invalide (%s) — valeur par défaut conservée",
                where, key, value, exc,
            )
    if required:
        kwargs.update(required)
    try:
        return klass(**kwargs)
    except Exception as exc:            # noqa: BLE001 — dernier filet
        log.error("« %s » illisible (%s) — valeurs par défaut", where, exc)
        return klass(**(required or {}))


@dataclass
class Settings:
    keywords: list[KeywordSettings] = field(default_factory=list)
    mercari: MercariSettings = field(default_factory=MercariSettings)
    scanner: ScannerSettings = field(default_factory=ScannerSettings)
    filters: FilterSettings = field(default_factory=FilterSettings)
    notifications: NotificationSettings = field(default_factory=NotificationSettings)
    storage: StorageSettings = field(default_factory=StorageSettings)
    scoring: ScoringSettings = field(default_factory=ScoringSettings)
    currency: CurrencySettings = field(default_factory=CurrencySettings)
    api_host: str = "127.0.0.1"
    api_port: int = 8899
    log_level: str = "INFO"

    # ── Secrets : jamais sérialisés ───────────────────────────────────────
    telegram_token: str = field(default="", repr=False)
    telegram_chat_id: str = field(default="", repr=False)
    #: Sujet d'un groupe Telegram en mode Forum. Facultatif.
    telegram_topic_id: str = field(default="", repr=False)
    discord_webhook: str = field(default="", repr=False)
    #: Fil (ou post de forum) visé dans le salon du webhook. Facultatif.
    discord_thread_id: str = field(default="", repr=False)

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
        if not isinstance(raw, dict):
            log.error(
                "radar.yaml ne contient pas un dictionnaire — valeurs par "
                "défaut utilisées"
            )
            raw = {}

        def section(name: str, klass):
            return build(klass, raw.get(name), where=name)

        keywords = []
        for index, item in enumerate(raw.get("keywords") or []):
            if isinstance(item, str):
                keywords.append(KeywordSettings(name=item))
            elif isinstance(item, dict) and item.get("name"):
                keywords.append(build(
                    KeywordSettings, item,
                    where=f"keywords[{index}]",
                    required={"name": str(item["name"])},
                ))
            else:
                log.warning(
                    "keywords[%s] ignoré : il lui faut au moins un « name »",
                    index,
                )


        return cls(
            keywords=keywords,
            scanner=section("scanner", ScannerSettings),
            filters=section("filters", FilterSettings),
            notifications=section("notifications", NotificationSettings),
            storage=section("storage", StorageSettings),
            mercari=section("mercari", MercariSettings),
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
        self.telegram_topic_id = os.getenv("TELEGRAM_TOPIC_ID", "").strip()
        self.discord_webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
        self.discord_thread_id = os.getenv("DISCORD_THREAD_ID", "").strip()

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
            "mercari": _clean(asdict(self.mercari)),
            "scanner": asdict(self.scanner),
            "filters": asdict(self.filters),
            "notifications": asdict(self.notifications),
            "storage": asdict(self.storage),
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
