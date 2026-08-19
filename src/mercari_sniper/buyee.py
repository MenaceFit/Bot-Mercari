"""Construction des liens Buyee.

Buyee est un service de proxy d'achat : il commande sur Mercari à ta place et
réexpédie à l'international. Depuis une annonce Mercari, on peut donc générer
directement l'URL de commande correspondante.

⚠ Le format d'URL n'a **pas** pu être vérifié en ligne depuis l'environnement
de développement (buyee.jp y est bloqué par la politique réseau). Les gabarits
sont donc **configurables** : si Buyee change de structure, ou si le défaut est
faux, une seule ligne de `config.yaml` suffit à corriger — sans toucher au code.
"""

from __future__ import annotations

import re
from urllib.parse import quote, urlencode

# Annonces de particuliers : identifiant `m` + chiffres.
DEFAULT_ITEM_TEMPLATE = "https://buyee.jp/item/mercari/item/{id}"
# Boutiques Mercari Shops : identifiant alphanumérique, autre route.
DEFAULT_SHOP_TEMPLATE = "https://buyee.jp/item/mercari/shops/{id}"

_CONSUMER_ID = re.compile(r"^m\d+$")


def is_consumer_item(item_id: str) -> bool:
    """Une annonce de particulier (`m123…`) plutôt qu'un produit de boutique."""
    return bool(_CONSUMER_ID.match(item_id))


def buyee_url(
    item_id: str,
    *,
    item_template: str = DEFAULT_ITEM_TEMPLATE,
    shop_template: str = DEFAULT_SHOP_TEMPLATE,
    affiliate_id: str = "",
    extra_params: dict[str, str] | None = None,
) -> str:
    """URL de commande Buyee pour une annonce Mercari.

    Renvoie une chaîne vide si l'identifiant est absent : l'appelant peut
    ainsi masquer le bouton plutôt que de produire un lien mort.
    """
    item_id = (item_id or "").strip()
    if not item_id:
        return ""

    template = item_template if is_consumer_item(item_id) else shop_template
    if not template:
        return ""

    url = template.replace("{id}", quote(item_id, safe=""))

    params: dict[str, str] = dict(extra_params or {})
    if affiliate_id:
        params["aid"] = affiliate_id
    if params:
        url = f"{url}{'&' if '?' in url else '?'}{urlencode(params)}"
    return url
