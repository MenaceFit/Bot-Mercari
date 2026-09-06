"""Contrat commun à tous les adapters de marketplace.

Un adapter sait faire quatre choses — démarrer, chercher, dire s'il va bien,
s'arrêter — et rend des `Listing` normalisés. Le reste du système ignore
totalement d'où vient une annonce.

État de support
---------------
Chaque adapter déclare son `SupportLevel`. C'est une exigence explicite du
cahier des charges : « si une source ne peut pas être surveillée directement
de manière fiable, marque-la UNSUPPORTED et explique pourquoi ». Ne jamais
simuler une source.

    VERIFIED         chemin complet exercé en conditions réelles
    URL_VERIFIED     structure d'URL établie sur preuves, extraction non testée
    NEEDS_SELECTORS  URL connue, sélecteurs à calibrer sur la machine cible
    UNSUPPORTED      surveillance impossible ou non fiable — raison obligatoire

Horodatages
-----------
    T0  created_at    publication côté marketplace
    T1  available_at  indexation par Buyee              (presque toujours inconnu)
    T2  requested_at  départ de notre requête
    T3  received_at   réponse reçue
    T4  matched_at    retenue par le filtre
    T5  notified_at   notification partie

Ce qui est inconnu reste à zéro. Une latence calculée sur un horodatage
absent serait une invention.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class SupportLevel(str, Enum):
    VERIFIED = "verified"
    URL_VERIFIED = "url_verified"
    NEEDS_SELECTORS = "needs_selectors"
    UNSUPPORTED = "unsupported"

    @property
    def usable(self) -> bool:
        return self is not SupportLevel.UNSUPPORTED


class AdapterError(RuntimeError):
    """Échec d'un adapter. `retry_after` renseigné sur un 429."""

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
        # Distingue « réessaie » de « ça ne marchera pas mieux dans 3 s » :
        # un 403 applicatif n'appelle pas la même réponse qu'un timeout.
        self.transient = transient

    @property
    def is_rate_limit(self) -> bool:
        return self.status == 429

    @property
    def is_blocked(self) -> bool:
        return self.status in (401, 403, 451)


@dataclass(slots=True)
class Listing:
    """Une annonce, identique quelle que soit la marketplace d'origine."""

    source: str
    listing_id: str
    title: str
    url: str

    description: str = ""
    price: int = 0
    currency: str = "JPY"
    image_url: str = ""
    seller: str = ""
    category: str = ""
    condition: str = ""
    availability: str = "on_sale"
    brand: str = ""

    # ── Horodatages (epoch s, 0.0 = inconnu) ──────────────────────────────
    created_at: float = 0.0       # T0
    available_at: float = 0.0     # T1
    requested_at: float = 0.0     # T2
    detected_at: float = 0.0      # T3
    matched_at: float = 0.0       # T4
    notified_at: float = 0.0      # T5
    updated_at: float = 0.0

    # ── Enrichissement local ──────────────────────────────────────────────
    keyword: str = ""
    keywords: list[str] = field(default_factory=list)
    score: int = 0
    tier: str = "NORMAL"
    buy_url: str = ""
    price_eur: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def key(self) -> str:
        """Clé primaire de déduplication : source + listing_id."""
        return f"{self.source}:{self.listing_id}"

    # ── Latences dérivées, en millisecondes ───────────────────────────────
    @property
    def network_ms(self) -> int:
        return _ms(self.requested_at, self.detected_at)

    @property
    def latency_ms(self) -> int:
        """T0 → T3. LE chiffre du cahier des charges.

        Inclut le délai d'indexation de la marketplace, sur lequel aucun
        code n'a de prise. Vaut 0 quand la source ne date pas ses annonces.
        """
        return _ms(self.created_at, self.detected_at)

    @property
    def pipeline_ms(self) -> int:
        return _ms(self.detected_at, self.matched_at)

    @property
    def notify_ms(self) -> int:
        return _ms(self.matched_at, self.notified_at)

    @property
    def end_to_end_ms(self) -> int:
        return _ms(self.created_at, self.notified_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "listing_id": self.listing_id,
            "key": self.key,
            "title": self.title,
            "description": self.description,
            "url": self.url,
            # Le lien PRINCIPAL de l'interface : on achète PAR Buyee.
            "buy_url": self.buy_url,
            # Le lien vers la marketplace d'origine, quand il est connu et
            # que l'identifiant correspond à sa forme. Second bouton
            # seulement — vide plutôt que faux.
            "origin_url": self.metadata.get("origin_url", ""),
            "via": self.metadata.get("via", ""),
            "price": self.price,
            "currency": self.currency,
            "price_eur": round(self.price_eur, 2) if self.price_eur else 0.0,
            "image_url": self.image_url,
            "seller": self.seller,
            "category": self.category,
            "condition": self.condition,
            "availability": self.availability,
            "brand": self.brand,
            "created_at": self.created_at,
            "detected_at": self.detected_at,
            "keyword": self.keyword,
            "keywords": list(self.keywords),
            "score": self.score,
            "tier": self.tier,
            "latency_ms": self.latency_ms,
            "network_ms": self.network_ms,
            "pipeline_ms": self.pipeline_ms,
            "notify_ms": self.notify_ms,
            "end_to_end_ms": self.end_to_end_ms,
        }


def _ms(start: float, end: float) -> int:
    """Écart en ms, 0 si un bout est inconnu. Arrondi, jamais tronqué."""
    if not start or not end or end < start:
        return 0
    return round((end - start) * 1000)


@dataclass(slots=True)
class SearchQuery:
    """Une recherche, indépendante de la marketplace."""

    text: str
    limit: int = 60
    min_price: int | None = None
    max_price: int | None = None
    category: str = ""
    #: Curseur natif de la source si elle en a un, sinon numéro de page.
    #: Renseigné UNIQUEMENT pour combler un trou détecté — jamais pour
    #: balayer systématiquement plusieurs pages.
    cursor: str = ""


@dataclass(slots=True)
class SearchResult:
    """Le retour d'un adapter. Jamais une exception nue : un `ok` explicite.

    Le cahier des charges l'exige (§32) : si une source change son format,
    l'application ne doit pas planter, et l'erreur doit rester visible.
    """

    source: str
    listings: list[Listing] = field(default_factory=list)
    ok: bool = True
    error: str = ""
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
class AdapterHealth:
    source: str
    ok: bool
    detail: str = ""
    latency_ms: int = 0
    support: SupportLevel = SupportLevel.VERIFIED
    checked_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "ok": self.ok,
            "detail": self.detail,
            "latency_ms": self.latency_ms,
            "support": self.support.value,
            "checked_at": self.checked_at,
        }


@runtime_checkable
class MarketplaceAdapter(Protocol):
    """Ce qu'une marketplace doit savoir faire pour entrer dans le système."""

    source: str
    label: str
    support: SupportLevel
    #: Pourquoi la source n'est pas pleinement supportée. Obligatoire dès
    #: que `support` n'est pas VERIFIED — sinon l'utilisateur ne peut pas
    #: savoir s'il doit faire confiance aux résultats.
    support_note: str

    async def start(self) -> None: ...

    async def search(self, query: SearchQuery) -> SearchResult: ...

    async def fetch_latest(self, query: SearchQuery) -> SearchResult:
        """Les annonces les plus récentes. Par défaut : `search` trié par date."""
        ...

    async def health_check(self) -> AdapterHealth: ...

    async def stop(self) -> None: ...
