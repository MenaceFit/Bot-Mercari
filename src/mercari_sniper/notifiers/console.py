"""Affichage console des trouvailles."""

from __future__ import annotations

import logging
import sys

from ..matching import PREMIUM, RARE, ULTRA
from ..models import Listing

log = logging.getLogger("sniper.hit")

_ANSI = {ULTRA[0]: "\033[91m", RARE[0]: "\033[93m", PREMIUM[0]: "\033[96m"}
_RESET = "\033[0m"


def _supports_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


class ConsoleNotifier:
    name = "console"

    def __init__(self, color: bool | None = None) -> None:
        self._color = _supports_color() if color is None else color

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def notify(self, listing: Listing) -> None:
        latency = f"{listing.latency_ms / 1000:.1f}s" if listing.latency_ms else "?"
        tag = listing.matched[0] if listing.matched else listing.source
        line = (
            f"[{listing.rarity}] ¥{listing.price:,} · {latency} · "
            f"{listing.title[:70]} · {tag} · {listing.url}"
        )
        if self._color:
            line = f"{_ANSI.get(listing.rarity, '')}{line}{_RESET}"
        log.info(line)

    def stats(self) -> dict:
        return {}
