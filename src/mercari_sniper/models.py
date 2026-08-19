"""Modèles de données du sniper."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, asdict
from typing import Any

_ITEM_ID_RE = re.compile(r"^m\d+$")


def item_url(item_id: str) -> str:
    """URL publique d'une annonce.

    Les annonces particuliers ont un id `m<chiffres>`; les boutiques Mercari
    Shops utilisent un id différent et une autre route.
    """
    if _ITEM_ID_RE.match(item_id):
        return f"https://jp.mercari.com/item/{item_id}"
    return f"https://jp.mercari.com/shops/product/{item_id}"


@dataclass(slots=True)
class Listing:
    """Une annonce détectée, normalisée quelle que soit la source."""

    id: str
    title: str
    price: int
    url: str
    image: str = ""
    seller_id: str = ""
    status: str = ""
    category_id: int = 0
    item_condition_id: int = 0

    # Horodatages
    created: int = 0          # epoch s — quand le vendeur a publié
    updated: int = 0

    # Enrichissement local
    source: str = ""          # la requête qui l'a ramenée
    matched: list[str] = field(default_factory=list)   # règles/keywords touchés
    rarity: str = ""
    rarity_color: int = 0x00D4AA
    buyee_url: str = ""    # lien de commande via le proxy d'achat
    detected_at: float = 0.0  # epoch s — quand NOUS l'avons vue

    @property
    def latency_ms(self) -> int:
        """Délai entre la publication par le vendeur et notre détection."""
        if not self.created or not self.detected_at:
            return 0
        return max(0, int((self.detected_at - self.created) * 1000))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["latency_ms"] = self.latency_ms
        return d

    @classmethod
    def from_api(cls, raw: dict[str, Any], source: str = "") -> "Listing":
        """Construit une Listing depuis la réponse brute de l'API Mercari v2.

        Tolérant aux champs manquants : l'API évolue et une annonce
        incomplète ne doit jamais faire tomber le scan.
        """
        item_id = str(raw.get("id") or "")
        thumbs = raw.get("thumbnails") or []

        def _int(value: Any) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0

        return cls(
            id=item_id,
            title=str(raw.get("name") or ""),
            price=_int(raw.get("price")),
            url=item_url(item_id),
            image=str(thumbs[0]) if thumbs else "",
            seller_id=str(raw.get("sellerId") or ""),
            status=str(raw.get("status") or ""),
            category_id=_int(raw.get("categoryId")),
            item_condition_id=_int(raw.get("itemConditionId")),
            created=_int(raw.get("created")),
            updated=_int(raw.get("updated")),
            source=source,
            detected_at=time.time(),
        )
