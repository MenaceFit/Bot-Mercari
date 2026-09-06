"""Adapter simulé : plusieurs marketplaces fictives, entièrement hors ligne.

Ce n'est pas une démonstration cosmétique, et ce n'est pas non plus une
« simulation de source » au sens que le cahier des charges interdit — il
interdit de faire passer une source fictive pour une vraie. Celle-ci
s'appelle `sim_*`, se déclare comme telle, et n'est jamais activée par
défaut en production.

Elle existe parce que :

* buyee.jp est inaccessible depuis l'environnement de développement ;
* même accessible, on ne pourrait pas y provoquer à la demande un trou de
  pagination, une salve, un 429 ou une panne — or ce sont précisément les
  chemins qu'il faut tester ;
* le mode `--benchmark` doit isoler le coût du pipeline local du bruit
  réseau.

Chaque marketplace simulée a sa propre cadence de publication et son propre
profil de latence, pour que le dashboard multi-sources soit exerçable.
"""

from __future__ import annotations

import asyncio
import itertools
import random
import time

from ..core.normalizer import normalize_text
from .base import (
    AdapterError,
    AdapterHealth,
    Listing,
    SearchQuery,
    SearchResult,
    SupportLevel,
)

_BRANDS = ["ナイキ", "NIKE", "アンダーアーマー", "adidas", "アークテリクス", "ノースフェイス"]
_LINES = [
    "ディビジョン", "division", "トレイル", "trail", "東京", "tokyo",
    "ACG", "ウィンドランナー", "ギャクソウ", "gyakusou", "テレックス", "veilance",
]
_KINDS = ["ジャケット", "パンツ", "ベスト", "パーカー", "tシャツ", "キャップ", "シューズ"]
_STATES = ["新品未使用", "美品", "used", "未使用に近い", "タグ付き"]
#: Bruit délibéré : sans lui, le rendement simulé serait irréaliste et le
#: filtre local n'aurait rien à écarter.
_NOISE = [
    "DIOR 香水 50ml", "シャネル オードパルファム", "資生堂 美容液",
    "ナイキ 空箱 のみ", "カタログ 2020", "ステッカー セット", "説明書のみ",
]

#: Profils par marketplace : cadence de publication et latence réseau.
PROFILES: dict[str, tuple[float, tuple[int, int]]] = {
    "sim_mercari": (3.0, (60, 180)),
    "sim_rakuma": (1.2, (90, 260)),
    "sim_jdirectitems_fleamarket": (0.8, (120, 340)),
    "sim_jdirectitems_auction": (0.5, (150, 420)),
}


class SimulatorAdapter:
    """Marketplace fictive, déterministe si on lui donne une graine."""

    support = SupportLevel.VERIFIED
    support_note = "source simulée, hors ligne — sert aux tests et au benchmark"

    def __init__(
        self,
        source: str = "sim_mercari",
        *,
        seed: int | None = None,
        new_per_second: float | None = None,
        latency_ms: tuple[int, int] | None = None,
        noise_ratio: float = 0.3,
        error_rate: float = 0.0,
    ) -> None:
        profile_rate, profile_latency = PROFILES.get(source, (1.5, (80, 220)))
        self.source = source
        self.label = source.replace("sim_", "").replace("_", " ").title() + " (simulé)"
        self._rng = random.Random(seed)
        self._rate = new_per_second if new_per_second is not None else profile_rate
        self._latency = latency_ms or profile_latency
        self._noise_ratio = noise_ratio
        self._error_rate = error_rate
        self._ids = itertools.count(1)
        self._t0 = time.time()
        #: Une horloge PAR REQUÊTE : sinon plusieurs requêtes sur la même
        #: source se volent le temps écoulé et n'en voient qu'une chacune.
        self._last: dict[str, float] = {}
        self._closed = False

    async def start(self) -> None:
        self._closed = False

    async def stop(self) -> None:
        self._closed = True

    def _make(self, now: float) -> Listing:
        rng = self._rng
        if rng.random() < self._noise_ratio:
            title = rng.choice(_NOISE)
        else:
            title = " ".join([
                rng.choice(_BRANDS), rng.choice(_LINES),
                rng.choice(_KINDS), rng.choice(_STATES),
            ])
        listing_id = f"{self.source[:3]}{next(self._ids):08d}"
        # Publication dans le passé récent : c'est ce délai qui rend la
        # latence de détection (T0 → T3) mesurable et non nulle.
        created = now - rng.uniform(0.4, 5.0)
        return Listing(
            source=self.source,
            listing_id=listing_id,
            title=title,
            url=f"https://example.invalid/{self.source}/item/{listing_id}",
            price=rng.choice([2800, 4500, 6900, 8900, 12500, 18500, 32000, 78000]),
            currency="JPY",
            seller=f"seller{rng.randint(1, 300)}",
            condition=rng.choice(["new", "like_new", "good"]),
            created_at=created,
            updated_at=created,
        )

    async def search(self, query: SearchQuery) -> SearchResult:
        if self._closed:
            return SearchResult(source=self.source, ok=False, error="adapter arrêté")
        if self._error_rate and self._rng.random() < self._error_rate:
            raise AdapterError("panne simulée", status=503)

        requested_at = time.time()
        await asyncio.sleep(self._rng.uniform(*self._latency) / 1000.0)
        received_at = time.time()

        since = self._last.get(query.text, self._t0)
        elapsed = max(0.0, received_at - since)
        expected = int(min(query.limit, max(1, elapsed * self._rate)))

        # La marketplace filtre côté serveur : le simulateur doit le faire
        # aussi, sinon le rendement affiché n'aurait aucun rapport avec la
        # réalité et le dégroupage automatique se déclencherait à tort.
        terms = normalize_text(query.text).split()
        listings: list[Listing] = []
        for _ in range(expected * 5):
            if len(listings) >= expected:
                break
            item = self._make(received_at)
            title = normalize_text(item.title)
            if terms and not all(term in title for term in terms):
                continue
            item.requested_at = requested_at
            item.detected_at = received_at
            listings.append(item)

        listings.sort(key=lambda item: item.created_at, reverse=True)
        self._last[query.text] = received_at

        return SearchResult(
            source=self.source,
            listings=listings,
            ok=True,
            cursor="sim-next" if len(listings) >= query.limit else "",
            requested_at=requested_at,
            received_at=received_at,
        )

    async def fetch_latest(self, query: SearchQuery) -> SearchResult:
        return await self.search(query)

    async def health_check(self) -> AdapterHealth:
        return AdapterHealth(
            source=self.source, ok=not self._closed,
            detail="simulateur local", support=self.support, latency_ms=1,
        )
