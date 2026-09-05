"""Buyee : proxy d'achat, et pourquoi ce n'est PAS une bonne source de détection.

Ce que Buyee est
----------------
Un service de procuration : il achète pour vous sur des marketplaces
japonaises réservées au marché intérieur (Yahoo Auctions, Mercari, Rakuten,
ZOZOTOWN et une trentaine d'autres) et réexpédie à l'international. Ses pages
de recherche sont un miroir de celles des marketplaces sous-jacentes.

Pourquoi le bot ne détecte pas SUR Buyee
----------------------------------------
Le critère numéro un du projet est la latence. Or Buyee est nécessairement en
retard sur la marketplace qu'il reflète : il faut que celle-ci publie, puis
que Buyee la récupère et la réindexe. Détecter via Buyee, c'est donc ajouter
un délai qu'on ne contrôle pas et qu'on ne peut pas mesurer, en plus d'un
saut réseau supplémentaire.

L'architecture retenue est l'inverse :

    détecter sur la marketplace source (au plus près de la publication)
              ↓
    construire l'URL Buyee correspondante pour l'achat

On garde le bénéfice de Buyee — pouvoir acheter depuis l'étranger — sans
payer son délai d'indexation.

Ce module fournit donc deux choses :

1. `buy_url()` — la construction du lien d'achat, utilisée après détection ;
2. `make_buyee_source()` — une source Buyee optionnelle, pour surveiller une
   marketplace que Buyee agrège mais qu'aucune source directe ne couvre.
   Utile comme rattrapage, jamais comme chemin rapide.

Vérification
------------
Les gabarits d'URL n'ont PAS pu être vérifiés : buyee.jp est inaccessible
depuis l'environnement de développement (403 au CONNECT de la passerelle).
Ils vivent donc dans la configuration : si un lien s'avère faux, la
correction se fait dans le YAML, sans toucher au code ni réinstaller quoi
que ce soit.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from .html_source import HTMLSource, SelectorSpec

log = logging.getLogger(__name__)

#: Gabarits par marketplace d'origine. `{id}` est remplacé par l'identifiant
#: de l'annonce tel que la source l'a fourni.
#: NON VÉRIFIÉS EN LIGNE — voir l'avertissement en tête de module.
DEFAULT_TEMPLATES: dict[str, str] = {
    "mercari": "https://buyee.jp/item/mercari/item/{id}",
    "mercari_shops": "https://buyee.jp/item/mercari/shops/{id}",
    "yahoo_auction": "https://buyee.jp/item/yahoo/auction/{id}",
    "yahoo_shopping": "https://buyee.jp/item/yahoo/shopping/{id}",
    "rakuma": "https://buyee.jp/item/rakuma/item/{id}",
}


def buy_url(
    source: str,
    listing_id: str,
    *,
    templates: dict[str, str] | None = None,
    affiliate_id: str = "",
) -> str:
    """Lien d'achat Buyee pour une annonce détectée ailleurs.

    Renvoie une chaîne vide si aucun gabarit ne correspond — mieux vaut pas
    de bouton qu'un bouton qui mène à une page d'erreur.
    """
    table = {**DEFAULT_TEMPLATES, **(templates or {})}
    template = table.get(source)
    if not template or not listing_id:
        return ""
    try:
        url = template.format(id=quote(str(listing_id), safe=""))
    except (KeyError, IndexError, ValueError):
        log.warning("gabarit Buyee invalide pour %s : %r", source, template)
        return ""
    if affiliate_id:
        url += ("&" if "?" in url else "?") + f"aid={quote(affiliate_id, safe='')}"
    return url


#: Sélecteurs CSS de la page de résultats Buyee.
#: ATTENTION — ils sont un POINT DE DÉPART, pas une vérité vérifiée. Le site
#: n'a jamais pu être chargé depuis l'environnement de développement. Ouvre
#: une page de recherche Buyee dans ton navigateur, inspecte le HTML, et
#: corrige ces valeurs dans config.yaml. La source journalise un
#: avertissement explicite tant qu'elle n'extrait rien.
SUGGESTED_SELECTORS = SelectorSpec(
    item="li.itemCard, div.item_card, li.product",
    title="a.itemCard__itemName, .item_card__title, .product__title",
    link="a",
    price=".itemCard__itemPrice, .item_card__price, .product__price",
    image="img",
    item_id_from_link=r"/item/[^/]+/(?:item|auction|shops)/([A-Za-z0-9_-]+)",
    next_page="a.pagination__next, a[rel=next]",
)


def make_buyee_source(
    *,
    marketplace: str = "mercari",
    search_url: str = "",
    selectors: SelectorSpec | None = None,
    **kwargs,
) -> HTMLSource:
    """Construit une source Buyee pour une marketplace agrégée.

    À n'utiliser que pour une marketplace qu'aucune source directe ne couvre :
    passer par Buyee ajoute son propre délai d'indexation à la détection.
    """
    return HTMLSource(
        name=f"buyee_{marketplace}",
        search_url=search_url or f"https://buyee.jp/{marketplace}/search?keyword={{keyword}}",
        selectors=selectors or SUGGESTED_SELECTORS,
        base_url="https://buyee.jp/",
        verified=False,
        **kwargs,
    )
