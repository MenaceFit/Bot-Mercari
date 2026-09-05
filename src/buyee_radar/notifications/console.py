"""Sortie console. Toujours disponible, y compris en `--dry-run`.

C'est le canal qui permet de vérifier ce que le bot trouve sans configurer
quoi que ce soit — donc le premier à consulter quand « il ne trouve rien ».
"""

from __future__ import annotations

import logging

from ..adapters.base import Listing

log = logging.getLogger("snipe.hit")


class ConsoleNotifier:
    name = "console"

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    async def send(self, listing: Listing) -> None:
        parts = [
            f"{listing.price:>9,} {listing.currency}".replace(",", " "),
            f"[{listing.source}]",
            listing.title[:70],
        ]
        if listing.keyword:
            parts.append(f"« {listing.keyword} »")
        # N'afficher une latence que si elle est mesurée, jamais un zéro
        # déguisé en « instantané ».
        if listing.latency_ms:
            parts.append(f"détectée en {listing.latency_ms / 1000:.1f}s")
        elif listing.network_ms:
            parts.append(f"réseau {listing.network_ms}ms")
        log.info("  ".join(parts))

    async def close(self) -> None:
        return None
