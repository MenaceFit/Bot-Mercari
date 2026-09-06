"""Un adapter nommé par source Buyee.

Chaque source a sa classe. Elles partagent le socle HTTP/parsing de
`BuyeeAdapter` — même client HTTP/2, même gestion des 429, même détection
de page de protection — mais chacune porte son URL, son extraction
d'identifiant et ses particularités.

Le cas intéressant est `CrossSearchAdapter` : une requête ramène des
annonces de cinq marketplaces mélangées, et il faut rendre à chacune la
sienne. Il ne se fie pas à un libellé affiché — trop fragile, et traduit
selon la langue de la page — mais au **namespace de l'URL de l'annonce**,
qui est ce que Buyee utilise lui-même pour router : `/mercari/item/…`
appartient à Mercari, point.
"""

from __future__ import annotations

import re
from typing import Any

from ..adapters.base import Listing, SupportLevel
from ..adapters.buyee_html import BuyeeAdapter, Marketplace, Selectors
from .registry import SOURCES, BuyeeSource, get


class BuyeeSourceAdapter(BuyeeAdapter):
    """Socle : relie une entrée du registre au moteur HTTP de Buyee."""

    #: Renseigné par chaque sous-classe. C'est la seule chose qu'elles
    #: doivent déclarer — tout le reste vient du registre.
    SOURCE_ID: str = ""

    def __init__(
        self,
        spec: BuyeeSource | None = None,
        *,
        selectors: Selectors | None = None,
        **kwargs: Any,
    ) -> None:
        if spec is None:
            spec = get(self.SOURCE_ID) if self.SOURCE_ID else None
        if spec is None:
            raise ValueError(
                f"source inconnue du registre : {self.SOURCE_ID!r} — "
                f"connues : {', '.join(sorted(SOURCES))}"
            )
        self.spec = spec
        super().__init__(
            Marketplace(
                source=spec.id,
                label=spec.label,
                search_url=spec.search_url,
                base_url=spec.base_url,
                id_from_url=spec.id_from_url,
                sort_newest=dict(spec.sort_newest),
                support=spec.support,
                support_note=spec.support_note,
            ),
            selectors=selectors,
            **kwargs,
        )

    @property
    def kind(self) -> str:
        return self.spec.kind

    @property
    def evidence(self) -> tuple[str, ...]:
        return self.spec.evidence

    def buy_link(self, listing_id: str, affiliate_id: str = "") -> str:
        return self.spec.buy_link(listing_id, affiliate_id)

    def origin_link(self, listing_id: str) -> str:
        return self.spec.origin_link(listing_id)

    def enrich(self, listing: Listing, affiliate_id: str = "") -> Listing:
        """Pose les deux liens. Buyee d'abord — c'est là qu'on achète."""
        spec = get(listing.source) or self.spec
        listing.buy_url = spec.buy_link(listing.listing_id, affiliate_id)
        origin = spec.origin_link(listing.listing_id)
        if origin:
            listing.metadata["origin_url"] = origin
        return listing


# ══════════════════════════════════════════════════════════════════════════
#  La métasource
# ══════════════════════════════════════════════════════════════════════════
#: Namespace d'URL → identifiant de source. Construit une seule fois à
#: partir du registre : ajouter une source suffit à l'attribuer.
_NAMESPACE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(s.id_from_url), s.id)
    for s in SOURCES.values() if s.id_from_url
)


def source_of_url(url: str) -> str:
    """À quelle marketplace appartient cette URL Buyee ? '' si indécidable."""
    for pattern, source_id in _NAMESPACE_PATTERNS:
        if pattern.search(url):
            return source_id
    return ""


class CrossSearchAdapter(BuyeeSourceAdapter):
    """`/item/crosssearch/query/{mot-clé}` — cinq marketplaces d'un coup.

    C'est la couche que Buyee expose lui-même, et donc le chemin le plus
    court vers « une requête, toutes les sources ». Les namespaces dédiés
    restent utiles pour paginer et trier source par source ; celui-ci donne
    la couverture.
    """

    SOURCE_ID = "crosssearch"

    def _listing(
        self, node: Any, base: str, requested_at: float, received_at: float
    ) -> Listing | None:
        listing = super()._listing(node, base, requested_at, received_at)
        if listing is None:
            return None

        # Rendre à chaque annonce sa marketplace. Sans attribution, un
        # résultat de cross-search serait rangé sous « crosssearch », ce qui
        # ne veut rien dire pour l'utilisateur — et casserait la
        # déduplication avec le namespace dédié de la même source.
        real = source_of_url(listing.url)
        if real:
            listing.source = real
            spec = SOURCES[real]
            match = re.search(spec.id_from_url, listing.url)
            if match:
                listing.listing_id = match.group(1)
        listing.metadata["via"] = "crosssearch"
        return listing

    @property
    def covers(self) -> tuple[str, ...]:
        return self.spec.aggregates


# ══════════════════════════════════════════════════════════════════════════
#  Les sources d'occasion — flux d'annonces, donc snipables
# ══════════════════════════════════════════════════════════════════════════
class MercariAdapter(BuyeeSourceAdapter):
    """`/mercari/search?keyword=` — une source parmi d'autres."""

    SOURCE_ID = "mercari"


class RakumaAdapter(BuyeeSourceAdapter):
    """`/rakuma/` — URL d'annonce confirmée, chemin de recherche déduit."""

    SOURCE_ID = "rakuma"


class JDirectItemsAuctionAdapter(BuyeeSourceAdapter):
    """`/item/search/query/` — le namespace le mieux attesté."""

    SOURCE_ID = "jdirectitems_auction"


class JDirectItemsFleamarketAdapter(BuyeeSourceAdapter):
    """`/paypayfleamarket/` — URL d'annonce confirmée."""

    SOURCE_ID = "jdirectitems_fleamarket"


class LuxeWholeSaleAdapter(BuyeeSourceAdapter):
    """Connue seulement par la liste du cross-search : pas d'URL propre.

    Aucune URL n'a été inventée pour l'interroger directement. Elle arrive
    par le cross-search, ou pas du tout.
    """

    SOURCE_ID = "luxewholesale"


# ══════════════════════════════════════════════════════════════════════════
#  Plateforme 2 — Mandarake
# ══════════════════════════════════════════════════════════════════════════
class MandarakeAdapter(BuyeeSourceAdapter):
    """`order.mandarake.co.jp` — enseigne d'occasion, hors Buyee.

    Deux particularités par rapport aux sources Buyee :

    * **Un vrai filtre de fraîcheur.** `upToMinutes=N` restreint aux
      articles arrivés dans les N dernières minutes. Là où ailleurs la
      nouveauté se déduit d'un filigrane d'horodatage, ici elle se demande.
      Utilisé pour le scan courant, désactivé pour le rattrapage — sinon
      un trou de dix minutes ne serait jamais comblé.
    * **L'achat est direct.** Mandarake expédie lui-même à l'international :
      il n'y a pas d'intermédiaire à intercaler, donc pas de second lien
      inventé.
    """

    SOURCE_ID = "mandarake"

    #: Fenêtre de fraîcheur du scan courant, en minutes. Assez large pour
    #: absorber un scan manqué, assez étroite pour ne pas re-télécharger
    #: tout le catalogue à chaque tour.
    fresh_window_minutes = 60

    def build_url(self, query, page: int = 1) -> str:
        url = super().build_url(query, page)
        # Pas de fenêtre sur un rattrapage : quand on comble un trou, on
        # veut justement ce qui est plus vieux que la fenêtre.
        if page <= 1 and "upToMinutes" not in url:
            url += ("&" if "?" in url else "?") + (
                f"upToMinutes={self.fresh_window_minutes}"
            )
        return url


# ══════════════════════════════════════════════════════════════════════════
#  Les catalogues — déclarés, expliqués, jamais simulés
# ══════════════════════════════════════════════════════════════════════════
class JDirectItemsShoppingAdapter(BuyeeSourceAdapter):
    SOURCE_ID = "jdirectitems_shopping"


class RakutenAdapter(BuyeeSourceAdapter):
    SOURCE_ID = "rakuten"


class AmazonAdapter(BuyeeSourceAdapter):
    SOURCE_ID = "amazon"


class ZozotownAdapter(BuyeeSourceAdapter):
    SOURCE_ID = "zozotown"


#: Le registre tel que le cahier des charges le décrit : un identifiant,
#: une classe. C'est cette table que le scanner parcourt.
ADAPTERS: dict[str, type[BuyeeSourceAdapter]] = {
    "crosssearch": CrossSearchAdapter,
    "mercari": MercariAdapter,
    "rakuma": RakumaAdapter,
    "jdirectitems_auction": JDirectItemsAuctionAdapter,
    "jdirectitems_fleamarket": JDirectItemsFleamarketAdapter,
    "luxewholesale": LuxeWholeSaleAdapter,
    "mandarake": MandarakeAdapter,
    "jdirectitems_shopping": JDirectItemsShoppingAdapter,
    "rakuten": RakutenAdapter,
    "amazon": AmazonAdapter,
    "zozotown": ZozotownAdapter,
}


def build(source_id: str, **kwargs: Any) -> BuyeeSourceAdapter:
    """Instancie l'adapter d'une source. Lève si elle est inconnue."""
    from .registry import resolve

    canonical = resolve(source_id)
    klass = ADAPTERS.get(canonical)
    if klass is None:
        raise ValueError(
            f"source inconnue : {source_id!r} — connues : "
            f"{', '.join(ADAPTERS)}"
        )
    return klass(**kwargs)


__all__ = [
    "ADAPTERS", "BuyeeSourceAdapter", "CrossSearchAdapter", "MercariAdapter",
    "RakumaAdapter", "JDirectItemsAuctionAdapter",
    "JDirectItemsFleamarketAdapter", "LuxeWholeSaleAdapter",
    "MandarakeAdapter",
    "JDirectItemsShoppingAdapter", "RakutenAdapter", "AmazonAdapter",
    "ZozotownAdapter", "build", "source_of_url", "SupportLevel",
]
