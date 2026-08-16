"""Backend simulé : génère de fausses annonces plausibles.

Sert à trois choses :
  * tester toute la chaîne (dedup, matching, dashboard, notifs) sans réseau ;
  * faire une démo du dashboard hors ligne (`--demo`) ;
  * valider le comportement sous erreurs (429, pannes) de façon déterministe.
"""

from __future__ import annotations

import asyncio
import itertools
import random
import time
from dataclasses import replace

from ..models import Listing, item_url
from .base import BackendError, SearchQuery

_TITLE_PARTS = [
    "ナイキ トレイル ウィンドランナー ジャケット",
    "ナイキ フェノム エリート パンツ",
    "NIKE ACG ベスト メンズ",
    "ナイキ ランニング ディビジョン 東京",
    "Nike Aeroloft 800 Vest",
    "アンダーアーマー コールドギア ハイブリッド",
    "Under Armour Storm Jacket",
    "ナイキ ギャクソウ ジャケット",
    "Nike Wild Run Windrunner Berlin",
    "ナイキ シールド ランニング パンツ",
]
_SUFFIXES = ["美品", "新品未使用", "Mサイズ", "Lサイズ", "希少", "完売品", ""]


class SimulatorBackend:
    """Fabrique des annonces à un rythme configurable."""

    name = "simulator"

    def __init__(
        self,
        *,
        new_items_per_minute: float = 30.0,
        latency_ms: tuple[int, int] = (40, 180),
        error_rate: float = 0.0,
        seed: int | None = None,
    ) -> None:
        self._rng = random.Random(seed)
        self._counter = itertools.count(1)
        self._latency_ms = latency_ms
        self._error_rate = error_rate
        # Intervalle moyen entre deux nouvelles annonces.
        self._spawn_interval = 60.0 / max(0.1, new_items_per_minute)
        self._next_spawn = time.time()
        self._lock = asyncio.Lock()
        # Catalogue initial : sans lui, la première recherche renverrait un
        # pool quasi vide et `once --demo` n'afficherait presque rien.
        self._pool: list[Listing] = [self._spawn() for _ in range(40)]

    def _spawn(self) -> Listing:
        index = next(self._counter)
        title = f"{self._rng.choice(_TITLE_PARTS)} {self._rng.choice(_SUFFIXES)}".strip()
        item_id = f"m{self._rng.randint(10**10, 10**11 - 1)}"
        now = time.time()
        return Listing(
            id=item_id,
            title=title,
            price=self._rng.choice([2800, 4500, 6900, 8900, 12000, 18500, 24000]),
            url=item_url(item_id),
            image="https://static.mercdn.net/images/placeholder.jpg",
            seller_id=f"sim{index}",
            status="ITEM_STATUS_ON_SALE",
            created=int(now),
            updated=int(now),
        )

    async def search(self, query: SearchQuery) -> list[Listing]:
        # Latence réseau simulée.
        low, high = self._latency_ms
        await asyncio.sleep(self._rng.randint(low, high) / 1000.0)

        if self._error_rate and self._rng.random() < self._error_rate:
            raise BackendError("erreur simulée (429)", status=429, retry_after=1.0)

        async with self._lock:
            now = time.time()
            # Rattrape toutes les annonces qui auraient dû naître depuis le
            # dernier appel, pour que le débit reste correct même si les
            # sources sont interrogées irrégulièrement.
            while self._next_spawn <= now:
                self._pool.append(self._spawn())
                self._next_spawn += self._spawn_interval
            if len(self._pool) > 500:
                self._pool = self._pool[-500:]

            newest = sorted(self._pool, key=lambda item: item.created, reverse=True)

        return [
            replace(listing, source=query.keyword, detected_at=time.time())
            for listing in newest[: query.page_size]
        ]

    async def aclose(self) -> None:
        return None
