"""Source simulée : marketplace fictive, entièrement hors ligne.

Elle n'est pas un gadget de démonstration. Les marketplaces réelles sont
inaccessibles depuis l'environnement de développement (la passerelle réseau
refuse le CONNECT vers api.mercari.jp, auctions.yahoo.co.jp et buyee.jp), et
même accessibles, elles ne permettraient pas de tester à la demande un trou
de pagination, une salve de publication ou un 429.

Cette source rend donc testable tout ce qui est en aval : planificateur,
ordonnanceur, déduplication, filtres, notifications, mesures de latence.
Elle sert aussi au mode `--benchmark`, où l'on veut isoler le coût du
pipeline local du bruit du réseau.

Elle produit des annonces dont le `published_at` est réaliste, ce qui permet
de vérifier que les latences se calculent correctement de bout en bout.
"""

from __future__ import annotations

import asyncio
import itertools
import random
import time

from ..core.normalizer import normalize_text
from .base import Listing, SearchQuery, SearchResult, SourceError, SourceHealth

_BRANDS = ["ナイキ", "NIKE", "アンダーアーマー", "adidas", "アークテリクス"]
_LINES = [
    "ディビジョン", "division", "トレイル", "trail", "東京", "tokyo",
    "ACG", "ウィンドランナー", "プロジェクトロック", "テレックス",
]
_KINDS = ["ジャケット", "パンツ", "ベスト", "パーカー", "tシャツ", "キャップ"]
_STATES = ["新品未使用", "美品", "used", "未使用に近い"]
#: Bruit délibéré : sans lui, le rendement simulé serait irréaliste et le
#: filtre local n'aurait rien à écarter.
_NOISE = [
    "DIOR 香水 50ml", "シャネル オードパルファム", "資生堂 美容液",
    "ナイキ 空箱 のみ", "カタログ 2020", "ステッカー セット",
]


class SimulatorSource:
    """Marketplace fictive, déterministe si on lui donne une graine."""

    name = "simulator"
    verified = True   # elle n'a pas de réseau à vérifier : c'est son intérêt

    def __init__(
        self,
        *,
        seed: int | None = None,
        latency_ms: tuple[int, int] = (40, 160),
        new_per_second: float = 1.5,
        noise_ratio: float = 0.35,
        error_rate: float = 0.0,
    ) -> None:
        self._rng = random.Random(seed)
        self._latency = latency_ms
        self._new_per_second = new_per_second
        self._noise_ratio = noise_ratio
        self._error_rate = error_rate
        self._ids = itertools.count(1)
        # Une horloge PAR REQUÊTE : sinon trois requêtes sur la même
        # source se volent mutuellement le temps écoulé, et chacune ne
        # voit qu'une annonce, ce qui ne ressemble à aucune marketplace.
        self._last: dict[str, float] = {}
        self._emitted: list[Listing] = []
        self._t0 = time.time()
        self._closed = False

    def _make(self, now: float) -> Listing:
        rng = self._rng
        if rng.random() < self._noise_ratio:
            title = rng.choice(_NOISE)
        else:
            title = " ".join([
                rng.choice(_BRANDS), rng.choice(_LINES),
                rng.choice(_KINDS), rng.choice(_STATES),
            ])
        item_id = f"sim{next(self._ids):08d}"
        # Publication située dans le passé récent : c'est ce délai qui rend
        # la latence de détection (T0 → T3) mesurable et non nulle.
        published = now - rng.uniform(0.5, 6.0)
        return Listing(
            id=item_id,
            source=self.name,
            title=title,
            url=f"https://example.invalid/item/{item_id}",
            price=rng.choice([2800, 4500, 6900, 8900, 12500, 18500, 32000]),
            currency="JPY",
            image_url="",
            seller=f"seller{rng.randint(1, 400)}",
            condition=rng.choice(["1", "2", "3"]),
            published_at=published,
            updated_at=published,
        )

    async def search(self, query: SearchQuery) -> SearchResult:
        if self._closed:
            raise SourceError("source fermée", transient=False)
        if self._error_rate and self._rng.random() < self._error_rate:
            raise SourceError("panne simulée", status=503)

        requested_at = time.time()
        await asyncio.sleep(self._rng.uniform(*self._latency) / 1000.0)
        received_at = time.time()

        # Le nombre d'annonces suit le temps écoulé : deux scans rapprochés
        # voient peu de nouveautés, un scan tardif en voit beaucoup. C'est ce
        # qui permet de reproduire un trou de pagination à la demande.
        since = self._last.get(query.text, self._t0)
        elapsed = max(0.0, received_at - since)
        expected = int(min(query.limit, max(1, elapsed * self._new_per_second)))

        # La marketplace filtre côté serveur : le simulateur doit le faire
        # aussi, sinon la démo affiche « ナイキ » trouvé par une requête
        # « アンダーアーマー » et donne une fausse idée du rendement réel.
        terms = normalize_text(query.text).split()
        listings = []
        for _ in range(expected * 4):          # on tire large, on garde ce qui colle
            if len(listings) >= expected:
                break
            item = self._make(received_at)
            title = normalize_text(item.title)
            if terms and not all(term in title for term in terms):
                continue
            listings.append(item)

        for item in listings:
            item.requested_at = requested_at
            item.received_at = received_at

        listings.sort(key=lambda item: item.published_at, reverse=True)
        self._emitted.extend(listings)
        self._last[query.text] = received_at

        return SearchResult(
            listings=listings,
            cursor="sim-next" if len(listings) >= query.limit else "",
            requested_at=requested_at,
            received_at=received_at,
        )

    async def health_check(self) -> SourceHealth:
        return SourceHealth(ok=not self._closed, detail="simulateur local")

    async def close(self) -> None:
        self._closed = True
