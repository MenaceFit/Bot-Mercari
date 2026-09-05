"""Yahoo! Auctions Japan.

État de l'accès
---------------
L'API de recherche publique du Yahoo! JAPAN Developer Network n'est plus
ouverte aux développeurs tiers pour les enchères. Il ne reste donc que la
lecture du HTML des pages de résultats.

Ce que je n'ai pas pu faire
---------------------------
`auctions.yahoo.co.jp` est inaccessible depuis l'environnement où ce code a
été écrit : la passerelle réseau refuse le CONNECT. Je n'ai donc jamais vu
la structure réelle de la page. Les sélecteurs ci-dessous sont un point de
départ plausible, pas une vérité vérifiée.

C'est pour cette raison précise que la source est pilotée par configuration
(`html_source.py`) : un sélecteur faux se corrige dans `config.yaml` en
regardant la page dans un navigateur, sans toucher au code. Et tant que rien
n'est extrait, la source le journalise bruyamment au lieu de renvoyer zéro
annonce en silence.

Elle est **désactivée par défaut** dans la configuration livrée.

Note sur la latence
-------------------
Une page de résultats n'affiche pas l'heure de publication exacte, seulement
la fin d'enchère. `published_at` reste donc inconnu pour cette source, et la
latence de détection (T0 → T3) n'est pas calculée — plutôt que d'être
approximée à partir d'une donnée qui ne veut pas dire ça.
"""

from __future__ import annotations

from .html_source import HTMLSource, SelectorSpec

#: Recherche triée par date de mise en vente décroissante. `s1=new` et
#: `o1=d` sont les paramètres de tri historiques du site — à confirmer sur
#: une page réelle.
SEARCH_URL = (
    "https://auctions.yahoo.co.jp/search/search"
    "?p={keyword}&s1=new&o1=d&n=50"
)

#: POINT DE DÉPART, NON VÉRIFIÉ — voir l'avertissement en tête de module.
SUGGESTED_SELECTORS = SelectorSpec(
    item="li.Product, .Product",
    title=".Product__titleLink, a.Product__titleLink",
    link="a.Product__titleLink, a",
    price=".Product__priceValue, .Product__price",
    image="img",
    seller=".Product__seller a",
    item_id_from_link=r"/auction/([a-zA-Z0-9]+)",
    next_page="a.Pager__link--next, a[rel=next]",
)


def make_yahoo_auction_source(
    *,
    search_url: str = SEARCH_URL,
    selectors: SelectorSpec | None = None,
    **kwargs,
) -> HTMLSource:
    return HTMLSource(
        name="yahoo_auction",
        search_url=search_url,
        selectors=selectors or SUGGESTED_SELECTORS,
        base_url="https://auctions.yahoo.co.jp/",
        verified=False,
        **kwargs,
    )
