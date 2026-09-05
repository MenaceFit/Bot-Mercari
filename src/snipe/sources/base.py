"""Contrat commun à toutes les sources.

Tout le reste du système ignore d'où vient une annonce. Une source expose
quatre choses — chercher, dire si elle va bien, se décrire, se fermer — et
rend des `Listing` normalisés. C'est ce qui permet d'ajouter Yahoo Auctions
ou une autre marketplace sans toucher au scanner, au filtre, ni au dashboard.

Les horodatages portés par `Listing` sont la matière première de la mesure
de latence. Ils suivent la nomenclature du cahier des charges :

    T0  published_at   publication supposée côté vendeur
    T1  available_at   moment où la source a exposé l'annonce (souvent
                       inconnu : la plupart des API ne le disent pas)
    T2  requested_at   départ de notre requête
    T3  received_at    réponse reçue
    T4  matched_at     annonce retenue par le filtre
    T5  notified_at    notification partie

Ce qui est inconnu reste à zéro. Une latence calculée sur un horodatage
absent serait une invention, et le cahier des charges est explicite là-dessus.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class SourceError(RuntimeError):
    """Échec d'une source. `retry_after` renseigné sur un 429."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        retry_after: float | None = None,
        transient: bool = True,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        # `transient` distingue « réessaie » de « ça ne marchera pas mieux
        # dans 3 secondes » : un 403 applicatif n'a pas la même réponse
        # qu'un timeout réseau.
        self.transient = transient

    @property
    def is_rate_limit(self) -> bool:
        return self.status == 429

    @property
    def is_blocked(self) -> bool:
        return self.status in (401, 403, 451)


@dataclass(slots=True)
class Listing:
    """Une annonce, normalisée quelle que soit la marketplace d'origine."""

    id: str
    source: str
    title: str
    url: str
    price: int = 0
    currency: str = "JPY"
    image_url: str = ""
    seller: str = ""
    category: str = ""
    condition: str = ""
    size: str = ""
    brand: str = ""

    # ── Horodatages (epoch secondes, 0.0 = inconnu) ───────────────────────
    published_at: float = 0.0     # T0
    available_at: float = 0.0     # T1
    requested_at: float = 0.0     # T2
    received_at: float = 0.0      # T3
    matched_at: float = 0.0       # T4
    notified_at: float = 0.0      # T5

    updated_at: float = 0.0

    # ── Enrichissement local ──────────────────────────────────────────────
    keyword: str = ""             # nom lisible du keyword touché
    keywords: list[str] = field(default_factory=list)
    buy_url: str = ""             # lien d'achat via le proxy (Buyee)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def key(self) -> str:
        """Clé de déduplication : une annonce est unique par source."""
        return f"{self.source}:{self.id}"

    # ── Latences dérivées, en millisecondes ───────────────────────────────
    @property
    def network_ms(self) -> int:
        """T2 → T3 : aller-retour réseau."""
        return _ms(self.requested_at, self.received_at)

    @property
    def detection_ms(self) -> int:
        """T0 → T3 : publication par le vendeur → réponse en notre possession.

        C'est le chiffre qui compte pour un sniper. Il inclut le délai
        d'indexation de la marketplace, sur lequel personne n'a de prise.
        """
        return _ms(self.published_at, self.received_at)

    @property
    def pipeline_ms(self) -> int:
        """T3 → T4 : coût de notre propre traitement une fois la page reçue."""
        return _ms(self.received_at, self.matched_at)

    @property
    def notify_ms(self) -> int:
        """T4 → T5 : filtre → notification partie."""
        return _ms(self.matched_at, self.notified_at)

    @property
    def total_ms(self) -> int:
        """T0 → T5, quand les deux bouts sont connus."""
        return _ms(self.published_at, self.notified_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "title": self.title,
            "url": self.url,
            "price": self.price,
            "currency": self.currency,
            "image_url": self.image_url,
            "seller": self.seller,
            "category": self.category,
            "condition": self.condition,
            "size": self.size,
            "brand": self.brand,
            "published_at": self.published_at,
            "received_at": self.received_at,
            "keyword": self.keyword,
            "keywords": list(self.keywords),
            "buy_url": self.buy_url,
            "network_ms": self.network_ms,
            "detection_ms": self.detection_ms,
            "pipeline_ms": self.pipeline_ms,
            "notify_ms": self.notify_ms,
            "total_ms": self.total_ms,
        }


def _ms(start: float, end: float) -> int:
    """Écart en ms, ou 0 si l'un des deux bouts est inconnu.

    Renvoyer 0 plutôt qu'une valeur plausible est délibéré : une latence
    inventée à partir d'un horodatage manquant fausserait les percentiles
    et donnerait une fausse impression de rapidité.
    """
    if not start or not end or end < start:
        return 0
    # Arrondi, pas troncature : `int()` retirerait jusqu'à 1 ms à CHAQUE
    # mesure, ce qui flatterait systématiquement les latences affichées.
    return round((end - start) * 1000)


@dataclass(slots=True)
class SearchQuery:
    """Une recherche, exprimée indépendamment de la source."""

    text: str
    limit: int = 60
    min_price: int | None = None
    max_price: int | None = None
    exclude_text: str = ""
    categories: list[str] = field(default_factory=list)
    # Pagination : renseigné uniquement quand on a détecté un trou. Le
    # monitoring temps réel vit sur la page 1 ; les pages suivantes ne sont
    # demandées que pour combler, jamais par balayage systématique.
    cursor: str = ""


@dataclass(slots=True)
class SearchResult:
    """Une page de résultats, avec de quoi continuer si nécessaire."""

    listings: list[Listing]
    cursor: str = ""
    requested_at: float = 0.0
    received_at: float = 0.0

    @property
    def latency_ms(self) -> int:
        return _ms(self.requested_at, self.received_at)

    def __len__(self) -> int:
        return len(self.listings)

    def __iter__(self):
        return iter(self.listings)


@dataclass(slots=True)
class SourceHealth:
    """Verdict d'un health check, lisible sans connaître la source."""

    ok: bool
    detail: str = ""
    latency_ms: int = 0
    checked_at: float = field(default_factory=time.time)


@runtime_checkable
class BaseSource(Protocol):
    """Ce qu'une marketplace doit savoir faire pour entrer dans le système."""

    name: str
    #: Une source non vérifiée en conditions réelles l'annonce elle-même.
    #: Le scanner le journalise au démarrage plutôt que de laisser croire
    #: que tout est éprouvé.
    verified: bool

    async def search(self, query: SearchQuery) -> SearchResult:
        """Renvoie une page d'annonces, de la plus récente à la plus ancienne."""
        ...

    async def health_check(self) -> SourceHealth:
        """Vérifie que la source répond, sans polluer les métriques de scan."""
        ...

    async def close(self) -> None:
        """Ferme les connexions. Idempotent."""
        ...


def buy_url_for(source: str, listing_id: str, templates: dict[str, str]) -> str:
    """Lien d'achat via le proxy, construit à partir d'un gabarit configurable.

    Les gabarits vivent dans la configuration, pas dans le code : le format
    d'URL de Buyee n'a pas pu être vérifié en ligne (le domaine est bloqué
    par la politique réseau de l'environnement de développement). Si un
    gabarit se révèle faux, la correction se fait dans le YAML.
    """
    template = templates.get(source, "")
    if not template or not listing_id:
        return ""
    try:
        return template.format(id=listing_id)
    except (KeyError, IndexError):
        return ""
