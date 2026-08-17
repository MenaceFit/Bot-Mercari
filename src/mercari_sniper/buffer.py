"""Tampon des annonces récemment scannées.

Raison d'être : la déduplication seule est *irréversible*. Une annonce écartée
parce qu'aucun keyword ne la touchait était marquée « vue » et ne pouvait plus
jamais être réévaluée — donc un keyword ajouté après coup ne trouvait rien de
ce qui avait déjà été scanné.

Ce tampon garde les annonces brutes des dernières minutes, indépendamment du
matching. Quand un keyword est ajouté, on le repasse sur le tampon et les
correspondances remontent immédiatement (« backfill ») au lieu d'attendre
qu'une nouvelle annonce arrive.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Iterable, Iterator

from .models import Listing


class RecentBuffer:
    """File bornée d'annonces brutes, purgée par âge et par taille."""

    __slots__ = ("_items", "_ids", "_window", "_max_items")

    def __init__(self, window_seconds: float = 1800.0, max_items: int = 6000) -> None:
        self._window = window_seconds
        self._max_items = max_items
        self._items: deque[Listing] = deque()
        self._ids: set[str] = set()

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[Listing]:
        return iter(self._items)

    @property
    def window_seconds(self) -> float:
        return self._window

    def add(self, listing: Listing) -> bool:
        """Ajoute une annonce. Renvoie False si elle y était déjà."""
        if listing.id in self._ids:
            return False
        self._items.append(listing)
        self._ids.add(listing.id)
        self._trim()
        return True

    def extend(self, listings: Iterable[Listing]) -> int:
        return sum(1 for listing in listings if self.add(listing))

    def _trim(self) -> None:
        cutoff = time.time() - self._window
        while self._items and (
            len(self._items) > self._max_items
            or self._items[0].detected_at < cutoff
        ):
            evicted = self._items.popleft()
            self._ids.discard(evicted.id)

    def prune(self) -> int:
        """Purge explicite par âge. Renvoie le nombre d'évictions."""
        before = len(self._items)
        self._trim()
        return before - len(self._items)

    def snapshot(self, newest_first: bool = True) -> list[Listing]:
        """Copie de travail, pour itérer sans risque de mutation concurrente."""
        items = list(self._items)
        if newest_first:
            items.reverse()
        return items
