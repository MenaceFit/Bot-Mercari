"""Le registre des sources Buyee.

MERCARI IS A SOURCE. BUyee IS THE TARGET SEARCH ECOSYSTEM.

Ce module est l'endroit — le seul — qui décrit *quoi* est atteignable à
travers Buyee. Chaque entrée porte ses preuves : les URL réellement
observées dans l'index des moteurs de recherche, puisque `buyee.jp` répond
403 au CONNECT depuis l'environnement de développement (voir BUYEE_AUDIT.md).

La découverte qui structure tout
--------------------------------
Buyee expose lui-même une recherche transversale :

    https://buyee.jp/item/crosssearch/query/{mot-clé}?lang=en

et la page se décrit ainsi, mot pour mot :

    « Supported Sites: JDirectItems Auction; JDirectItems Fleamarket;
      Rakuma; Mercari; LuxeWholeSale.
      You can search for products across multiple secondhand sites. »

Autrement dit : la couche multi-marketplace n'est pas à inventer, elle est
la plateforme. Une requête, cinq sources. Le namespace par source reste
utile — il permet de paginer et de trier source par source — mais il est
désormais le *complément* du cross-search, pas le point de départ.

Deux familles, et Buyee les sépare lui-même
-------------------------------------------
Les cinq sites d'occasion ci-dessus vivent sur `buyee.jp` et alimentent le
cross-search : ce sont des **flux d'annonces**, ce qu'on peut sniper.

Rakuten, Amazon et ZOZOTOWN vivent sur `shop.buyee.jp`, rangés par boutique
partenaire et par catégorie, et sont **absents de la liste cross-search**.
Ce sont des **catalogues marchands** : la fiche produit est durable et
réapprovisionnée, il n'y a pas de « nouvelle annonce » à détecter. Les
surveiller pour du sniping n'aurait pas de sens — elles sont déclarées
UNSUPPORTED avec ce motif, jamais simulées.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..adapters.base import SupportLevel

#: Familles de sources. Détermine ce qu'on peut espérer d'une source, pas
#: seulement comment on l'interroge.
KIND_C2C = "c2c"            # particuliers, flux continu   → sniping pertinent
KIND_AUCTION = "auction"    # enchères, flux continu       → sniping pertinent
KIND_META = "meta"          # agrège plusieurs sources
KIND_CATALOG = "catalog"    # catalogue marchand, pas de flux → hors sujet


@dataclass(slots=True)
class BuyeeSource:
    """Une source atteignable — ou non — à travers Buyee.

    `evidence` n'est pas décoratif : c'est ce qui distingue une URL observée
    d'une URL supposée. Le dashboard l'affiche, et un test vérifie qu'aucune
    source ne se déclare mieux supportée que ses preuves ne le permettent.
    """

    id: str
    label: str
    kind: str
    #: Gabarit de recherche. Vide = pas de recherche par mot-clé connue.
    search_url: str = ""
    base_url: str = "https://buyee.jp/"
    #: Extraction de l'identifiant depuis l'URL d'une annonce.
    id_from_url: str = ""
    #: Gabarit du lien d'achat Buyee. C'est le lien PRINCIPAL de l'interface.
    buy_url: str = ""
    #: Gabarit du lien vers la marketplace d'origine, quand il est connu.
    #: Second bouton seulement — l'utilisateur passe par Buyee pour acheter.
    origin_url: str = ""
    #: L'identifiant doit correspondre, sinon on n'émet pas de lien d'origine
    #: plutôt que d'en fabriquer un faux.
    origin_id_pattern: str = ""
    #: Tri par date décroissante. HYPOTHÈSE tant qu'une page n'a pas été vue.
    sort_newest: dict[str, str] = field(default_factory=dict)
    #: Couverte par /item/crosssearch/ d'après Buyee lui-même.
    in_crosssearch: bool = False
    #: Sources agrégées, pour les métasources uniquement.
    aggregates: tuple[str, ...] = ()
    support: SupportLevel = SupportLevel.NEEDS_SELECTORS
    support_note: str = ""
    #: URL réellement observées dans l'index. La preuve, ou son absence.
    evidence: tuple[str, ...] = ()

    @property
    def searchable(self) -> bool:
        return bool(self.search_url) and self.support.usable

    def origin_link(self, listing_id: str) -> str:
        """Lien vers la marketplace d'origine, ou rien.

        Rien, plutôt qu'un lien plausible : un bouton « Open original » qui
        mène à une 404 est pire que pas de bouton du tout.
        """
        if not (self.origin_url and listing_id):
            return ""
        if self.origin_id_pattern and not re.fullmatch(
            self.origin_id_pattern, listing_id
        ):
            return ""
        return self.origin_url.format(id=listing_id)

    def buy_link(self, listing_id: str, affiliate_id: str = "") -> str:
        if not (self.buy_url and listing_id):
            return ""
        url = self.buy_url.format(id=listing_id)
        if affiliate_id:
            url += ("&" if "?" in url else "?") + f"aid={affiliate_id}"
        return url


# ══════════════════════════════════════════════════════════════════════════
#  LE REGISTRE
# ══════════════════════════════════════════════════════════════════════════
SOURCES: dict[str, BuyeeSource] = {
    # ── La métasource : une requête, cinq marketplaces ────────────────────
    "crosssearch": BuyeeSource(
        id="crosssearch",
        label="Buyee Cross-Search",
        kind=KIND_META,
        search_url="https://buyee.jp/item/crosssearch/query/{keyword}?lang=en",
        buy_url="",  # les résultats portent déjà leur URL Buyee
        in_crosssearch=True,
        aggregates=(
            "jdirectitems_auction", "jdirectitems_fleamarket",
            "rakuma", "mercari", "luxewholesale",
        ),
        support=SupportLevel.URL_VERIFIED,
        support_note=(
            "Recherche transversale de Buyee. La page déclare elle-même ses "
            "sites supportés : JDirectItems Auction, JDirectItems "
            "Fleamarket, Rakuma, Mercari, LuxeWholeSale. Une requête couvre "
            "les cinq. Le sélecteur qui étiquette la source de chaque "
            "résultat se découvre par calibration."
        ),
        evidence=(
            "https://buyee.jp/item/crosssearch/query/{kw}?lang=en",
        ),
    ),

    # ── Les cinq sources du cross-search ──────────────────────────────────
    "mercari": BuyeeSource(
        id="mercari",
        label="Mercari",
        kind=KIND_C2C,
        search_url=(
            "https://buyee.jp/mercari/search"
            "?keyword={keyword}&page={page}&lang=en"
        ),
        id_from_url=r"/mercari/item/([A-Za-z0-9_-]+)",
        buy_url="https://buyee.jp/mercari/item/{id}",
        origin_url="https://jp.mercari.com/item/{id}",
        origin_id_pattern=r"m\d{9,}",
        sort_newest={"status": "on_sale"},
        in_crosssearch=True,
        support=SupportLevel.URL_VERIFIED,
        support_note=(
            "Paramètre keyword= attesté par de nombreuses URL indexées, "
            "avec page= et status=on_sale. Une source parmi d'autres — pas "
            "la source principale."
        ),
        evidence=(
            "https://buyee.jp/mercari/search?keyword=Gibraltar%20mail&lang=en",
            "https://buyee.jp/mercari/search?keyword=...&status=on_sale",
            "https://www.buyee.jp/mercari/search?seller=968116560&page=2",
        ),
    ),
    "rakuma": BuyeeSource(
        id="rakuma",
        label="Rakuma",
        kind=KIND_C2C,
        search_url=(
            "https://buyee.jp/rakuma/search"
            "?keyword={keyword}&page={page}&lang=en"
        ),
        id_from_url=r"/rakuma/item/([A-Za-z0-9]+)",
        buy_url="https://buyee.jp/rakuma/item/{id}",
        in_crosssearch=True,
        support_note=(
            "Les URL d'ANNONCE sont confirmées (/rakuma/item/{hex32}), mais "
            "AUCUNE URL de recherche /rakuma/search n'apparaît dans l'index "
            "— alors que celles de Mercari y sont nombreuses. Le chemin est "
            "donc déduit par symétrie, pas observé. Le cross-search couvre "
            "Rakuma en attendant."
        ),
        evidence=(
            "https://buyee.jp/rakuma/item/372996ff8780f443c723133905af67d4",
            "https://buyee.jp/rakuma/item/{hex32}?conversionType=crosssearch",
            "https://buyee.jp/rakuma/?lang=en",
        ),
    ),
    "jdirectitems_auction": BuyeeSource(
        id="jdirectitems_auction",
        label="JDirectItems Auction",
        kind=KIND_AUCTION,
        search_url=(
            "https://buyee.jp/item/search/query/{keyword}?page={page}&lang=en"
        ),
        id_from_url=r"/item/jdirectitems/auction/([A-Za-z0-9]+)",
        buy_url="https://buyee.jp/item/jdirectitems/auction/{id}",
        origin_url="https://page.auctions.yahoo.co.jp/jp/auction/{id}",
        origin_id_pattern=r"[a-z]\d{6,}",
        # `sort=end&order=` est attesté ; la valeur pour « les plus récentes »
        # ne l'est pas. Surchargeable dans radar.yaml.
        sort_newest={"sort": "end", "order": "d"},
        in_crosssearch=True,
        support=SupportLevel.URL_VERIFIED,
        support_note=(
            "La source la mieux attestée : /item/search/query/{mot-clé} "
            "apparaît dans des dizaines d'URL indexées, avec page=, "
            "sort=bids|bidorbuy|end, order=, /category/{id} et closed=1. "
            "C'est le namespace des enchères (ex-Yahoo! Auctions)."
        ),
        evidence=(
            "https://buyee.jp/item/search/query/photocards?lang=en",
            "https://buyee.jp/item/search/query/z/category/2084063431"
            "?sort=bids&order=asc&page=1&lang=en",
            "https://buyee.jp/item/search/query/%E5%BC%A5%E4%BD%9C"
            "/category/24642?sort=end&order=...",
            "https://buyee.jp/jdirectitems/auction",
        ),
    ),
    "jdirectitems_fleamarket": BuyeeSource(
        id="jdirectitems_fleamarket",
        label="JDirectItems Fleamarket",
        kind=KIND_C2C,
        search_url=(
            "https://buyee.jp/paypayfleamarket/search"
            "?keyword={keyword}&page={page}&lang=en"
        ),
        id_from_url=r"/paypayfleamarket/item/([A-Za-z0-9]+)",
        buy_url="https://buyee.jp/paypayfleamarket/item/{id}",
        origin_url="https://paypayfleamarket.yahoo.co.jp/item/{id}",
        origin_id_pattern=r"z\d{6,}",
        in_crosssearch=True,
        support_note=(
            "Les URL d'ANNONCE sont confirmées "
            "(/paypayfleamarket/item/z{chiffres}), mais aucune URL de "
            "recherche ne figure dans l'index. Chemin déduit par symétrie. "
            "Le cross-search couvre cette source en attendant."
        ),
        evidence=(
            "https://buyee.jp/paypayfleamarket/item/z488929156",
            "https://buyee.jp/paypayfleamarket/item/z610902210?lang=en",
            "https://buyee.jp/paypayfleamarket/?lang=en",
        ),
    ),
    "luxewholesale": BuyeeSource(
        id="luxewholesale",
        label="LuxeWholeSale",
        kind=KIND_C2C,
        # Aucun namespace propre observé : cette source n'est connue que
        # parce que le cross-search la déclare. On ne devine pas d'URL.
        search_url="",
        in_crosssearch=True,
        support_note=(
            "Déclarée par la page cross-search de Buyee parmi ses sites "
            "supportés, mais aucun namespace /luxewholesale/ n'apparaît "
            "dans l'index. Atteignable UNIQUEMENT via le cross-search — "
            "aucune URL n'a été inventée pour l'interroger directement."
        ),
        evidence=(
            "Liste « Supported Sites » de "
            "https://buyee.jp/item/crosssearch/query/{kw}",
        ),
    ),

    # ── Les catalogues : hors sujet, et Buyee le dit lui-même ─────────────
    "jdirectitems_shopping": BuyeeSource(
        id="jdirectitems_shopping",
        label="JDirectItems Shopping",
        kind=KIND_CATALOG,
        search_url="",
        support=SupportLevel.UNSUPPORTED,
        support_note=(
            "Organisé par boutique : /jdirectitems/shopping/store/top/"
            "{boutique}. Absent de la liste cross-search de Buyee. Un "
            "catalogue de boutique n'est pas un flux de nouveautés — il n'y "
            "a rien à sniper."
        ),
        evidence=(
            "https://buyee.jp/jdirectitems/shopping?lang=en",
            "https://buyee.jp/jdirectitems/shopping/store/top/c-well3?lang=en",
        ),
    ),
    "rakuten": BuyeeSource(
        id="rakuten",
        label="Rakuten",
        kind=KIND_CATALOG,
        base_url="https://shop.buyee.jp/",
        search_url="",
        support=SupportLevel.UNSUPPORTED,
        support_note=(
            "Vit sur shop.buyee.jp, rangé par boutique partenaire et par "
            "catégorie (/{partenaire}/shopping/search/category/{id}), et "
            "absent de la liste cross-search de Buyee. Catalogue de "
            "marchands : fiches durables et réapprovisionnées, pas de flux "
            "de nouvelles annonces."
        ),
        evidence=(
            "https://shop.buyee.jp/bookoff/shopping/search/category/a50182",
            "https://shop.buyee.jp/?lang=en  (jeton « shop_rakuten »)",
        ),
    ),
    "amazon": BuyeeSource(
        id="amazon",
        label="Amazon",
        kind=KIND_CATALOG,
        base_url="https://shop.buyee.jp/",
        search_url="",
        support=SupportLevel.UNSUPPORTED,
        support_note=(
            "Même famille que Rakuten : catalogue marchand sur "
            "shop.buyee.jp, absent du cross-search. Aucune recherche par "
            "mot-clé à l'échelle du site n'a pu être établie, et un "
            "catalogue neuf n'a pas de flux d'annonces à surveiller."
        ),
        evidence=("https://shop.buyee.jp/?lang=en  (jeton « amazon »)",),
    ),
    "zozotown": BuyeeSource(
        id="zozotown",
        label="ZOZOTOWN",
        kind=KIND_CATALOG,
        base_url="https://shop.buyee.jp/",
        search_url="",
        support=SupportLevel.UNSUPPORTED,
        support_note=(
            "Catalogue de mode neuve sur shop.buyee.jp (jeton "
            "« shop_zozotown »), absent du cross-search. No usable public "
            "keyword-search interface established, and a retail catalogue "
            "has no new-listing flow to monitor."
        ),
        evidence=("https://shop.buyee.jp/?lang=en  (jeton « shop_zozotown »)",),
    ),
}

#: Ordre d'affichage : les métasources d'abord, puis les flux, puis les
#: catalogues. Le dashboard suit cet ordre, il n'en réinvente pas un.
DISPLAY_ORDER: tuple[str, ...] = (
    "crosssearch",
    "mercari",
    "rakuma",
    "jdirectitems_auction",
    "jdirectitems_fleamarket",
    "luxewholesale",
    "jdirectitems_shopping",
    "rakuten",
    "amazon",
    "zozotown",
)

#: Anciens identifiants → nouveaux. Une config existante continue de
#: fonctionner, avec un avertissement plutôt qu'un plantage.
ALIASES: dict[str, str] = {
    "jdi_auction": "jdirectitems_auction",
    "jdi_fleamarket": "jdirectitems_fleamarket",
    "jdi_shopping": "jdirectitems_shopping",
}


def resolve(source_id: str) -> str:
    """Identifiant canonique d'une source, alias compris."""
    return ALIASES.get(source_id, source_id)


def get(source_id: str) -> BuyeeSource | None:
    return SOURCES.get(resolve(source_id))


def all_sources() -> list[BuyeeSource]:
    return [SOURCES[name] for name in DISPLAY_ORDER if name in SOURCES]


def searchable_ids() -> list[str]:
    """Sources qu'on peut réellement interroger par mot-clé."""
    return [s.id for s in all_sources() if s.searchable]
