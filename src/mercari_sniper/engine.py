"""Moteur de détection : ordonnancement, déduplication, dispatch.

Modèle d'exécution
------------------
Une tâche asyncio par source, chacune bouclant sur son propre rythme et
partageant un token bucket global. Trois propriétés importantes :

1. **Ordonnancement sans dérive** — l'échéance suivante est calculée depuis
   la précédente (`deadline += interval`), pas depuis la fin du travail.
   Sinon le temps de requête s'ajoute à chaque tour et l'intervalle réel
   dérive (le bot v1 dérivait de plusieurs secondes par heure).

2. **Dédup en mémoire** — un `set` amorcé depuis SQLite. Aucune I/O sur le
   chemin critique ; les écritures partent en lot dans un thread.

3. **Backoff coopératif** — un 429 sur une source réduit le débit global,
   parce que la limite est côté IP : ralentir une seule source ne sert à rien.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .backends.base import BackendError, SearchBackend, SearchQuery
from .config import Config, SourceConfig
from .events import EventBus
from .matching import Matcher, Rule, normalize, rarity_of
from .models import Listing
from .ratelimit import TokenBucket
from .store import Store

log = logging.getLogger(__name__)


@dataclass
class SourceState:
    """État vivant d'une source, exposé au dashboard."""

    config: SourceConfig
    interval: float
    warmed_up: bool = False
    polls: int = 0
    errors: int = 0
    consecutive_errors: int = 0
    hits: int = 0
    last_poll: float = 0.0
    last_error: str = ""
    last_duration_ms: int = 0
    paused: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.config.query,
            "enabled": self.config.enabled and not self.paused,
            "paused": self.paused,
            "interval": round(self.interval, 2),
            "polls": self.polls,
            "errors": self.errors,
            "hits": self.hits,
            "warmed_up": self.warmed_up,
            "last_poll": self.last_poll,
            "last_error": self.last_error,
            "last_duration_ms": self.last_duration_ms,
        }


class SniperEngine:
    """Orchestre les sources, le matching et la diffusion des trouvailles."""

    def __init__(
        self,
        config: Config,
        backend: SearchBackend,
        store: Store,
        bus: EventBus,
        notifiers: list[Any] | None = None,
    ) -> None:
        self.config = config
        self.backend = backend
        self.store = store
        self.bus = bus
        self.notifiers = notifiers or []

        self.matcher = Matcher(
            [
                Rule.compile(
                    keyword,
                    min_price=config.filters.min_price,
                    max_price=config.filters.max_price,
                )
                for keyword in config.keywords
            ]
        )

        self._bucket = TokenBucket(config.poll.global_rate_limit)
        self._base_rate = config.poll.global_rate_limit
        self._seen: set[str] = set()
        self._seen_order: deque[str] = deque()
        self._sources: dict[str, SourceState] = {}
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self._started_at = 0.0
        self._throttle_until = 0.0

        # Compteurs globaux
        self.total_polls = 0
        self.total_items_seen = 0
        self.total_hits = 0
        self._latencies: deque[int] = deque(maxlen=500)

        self.feed: deque[dict[str, Any]] = deque(maxlen=config.server.max_feed)

        for source in config.sources:
            self._sources[source.query] = SourceState(
                config=source,
                interval=self._interval_for(source),
            )

    # ── Paramétrage ───────────────────────────────────────────────────────
    def _interval_for(self, source: SourceConfig) -> float:
        if source.interval is not None:
            return max(self.config.poll.min_interval, source.interval)
        # Un poids élevé (racine couvrant beaucoup de keywords) => plus fréquent.
        interval = self.config.poll.interval / max(0.1, source.weight)
        return max(self.config.poll.min_interval, interval)

    @property
    def running(self) -> bool:
        return self._running

    # ── Cycle de vie ──────────────────────────────────────────────────────
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._started_at = time.time()

        self._seen = await self.store.load_seen_ids(self.config.storage.seen_cache_size)
        self._seen_order = deque(self._seen)
        log.info("cache de déduplication amorcé: %d ids", len(self._seen))

        for notifier in self.notifiers:
            await notifier.start()

        active = [s for s in self._sources.values() if s.config.enabled]
        log.info(
            "démarrage: %d sources, %d keywords, budget %.1f req/s",
            len(active),
            len(self.config.keywords),
            self._bucket.rate,
        )

        for index, state in enumerate(active):
            # Décalage initial : évite que toutes les sources partent ensemble.
            delay = (index / max(1, len(active))) * self.config.poll.interval
            self._tasks.append(
                asyncio.create_task(
                    self._source_loop(state, delay), name=f"source:{state.config.query}"
                )
            )
        self._tasks.append(asyncio.create_task(self._maintenance_loop(), name="maint"))

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        await self.store.flush()
        for notifier in self.notifiers:
            await notifier.stop()
        log.info("moteur arrêté")

    # ── Boucle par source ─────────────────────────────────────────────────
    async def _source_loop(self, state: SourceState, initial_delay: float) -> None:
        try:
            await asyncio.sleep(initial_delay)
            deadline = time.monotonic()

            while self._running:
                if state.paused:
                    await asyncio.sleep(0.5)
                    deadline = time.monotonic()
                    continue

                try:
                    found = await self._poll_source(state)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("erreur inattendue sur %s", state.config.query)
                    found = 0

                interval = state.interval
                # Une trouvaille signale une salve du vendeur : on repasse tout
                # de suite, les lots multi-annonces arrivent groupés.
                if found and self.config.poll.burst_on_hit:
                    interval = self.config.poll.min_interval

                jitter = 1.0 + random.uniform(
                    -self.config.poll.jitter, self.config.poll.jitter
                )
                deadline += max(0.05, interval * jitter)

                sleep_for = deadline - time.monotonic()
                if sleep_for < 0:
                    # On a pris du retard : on repart de maintenant plutôt que
                    # d'accumuler une dette qui ferait boucler sans pause.
                    deadline = time.monotonic()
                    sleep_for = 0
                await asyncio.sleep(sleep_for)
        except asyncio.CancelledError:
            pass

    async def _poll_source(self, state: SourceState) -> int:
        # Attente globale (partagée) + éventuel throttle après un 429.
        now = time.monotonic()
        if now < self._throttle_until:
            await asyncio.sleep(self._throttle_until - now)
        await self._bucket.acquire()

        query = SearchQuery(
            keyword=state.config.query,
            page_size=state.config.page_size,
            min_price=state.config.min_price or self.config.filters.min_price,
            max_price=state.config.max_price or self.config.filters.max_price,
            exclude_keyword=state.config.exclude_keyword,
        )

        started = time.perf_counter()
        try:
            listings = await self.backend.search(query)
        except BackendError as exc:
            self._handle_error(state, exc)
            return 0

        state.last_duration_ms = int((time.perf_counter() - started) * 1000)
        state.polls += 1
        state.last_poll = time.time()
        state.consecutive_errors = 0
        self.total_polls += 1

        return await self._process(state, listings)

    def _handle_error(self, state: SourceState, exc: BackendError) -> None:
        state.errors += 1
        state.consecutive_errors += 1
        state.last_error = str(exc)[:200]

        if exc.is_rate_limit:
            pause = exc.retry_after or 5.0
            self._throttle_until = time.monotonic() + pause
            # La limite est par IP : on baisse le débit global, pas celui d'une source.
            new_rate = max(0.5, self._bucket.rate * 0.7)
            self._bucket.set_rate(new_rate)
            log.warning(
                "429 sur '%s' — pause %.1fs, débit global ramené à %.2f req/s",
                state.config.query,
                pause,
                new_rate,
            )
        elif exc.is_blocked:
            log.error("accès bloqué sur '%s': %s", state.config.query, exc)
            state.interval = min(self.config.poll.max_interval, state.interval * 2)
        else:
            log.warning("erreur '%s': %s", state.config.query, exc)

        # Une source durablement en échec s'espace au lieu de brûler le budget.
        if state.consecutive_errors >= 3:
            state.interval = min(
                self.config.poll.max_interval, state.interval * 1.5
            )

        self.bus.publish(
            "source_error",
            {"query": state.config.query, "error": state.last_error},
        )

    # ── Traitement des résultats ──────────────────────────────────────────
    async def _process(self, state: SourceState, listings: list[Listing]) -> int:
        if not listings:
            return 0

        self.total_items_seen += len(listings)

        fresh = [item for item in listings if item.id not in self._seen]
        for item in fresh:
            self._remember(item.id)

        # Premier passage : on mémorise sans notifier, sinon les 120 annonces
        # déjà en ligne partiraient toutes en notification.
        if not state.warmed_up:
            state.warmed_up = True
            if self.config.poll.warmup and fresh:
                await self.store.mark_seen_bulk(item.id for item in fresh)
                log.info(
                    "warmup '%s': %d annonces mémorisées (aucune notification)",
                    state.config.query,
                    len(fresh),
                )
                return 0

        if not fresh:
            return 0

        max_age = self.config.filters.max_age_seconds
        now = time.time()
        hits = 0

        for listing in fresh:
            # Garde-fou temporel : une annonce ancienne qui apparaît pour la
            # première fois (nouveau keyword, cache purgé) n'est pas une news.
            if max_age and listing.created and (now - listing.created) > max_age:
                continue

            matched = self.matcher.match(listing.title, listing.price)
            if not matched:
                continue
            if self._is_excluded(listing.title):
                continue

            listing.matched = matched
            listing.rarity, listing.rarity_color = rarity_of(listing.title, matched[0])

            hits += 1
            state.hits += 1
            self.total_hits += 1
            if listing.latency_ms:
                self._latencies.append(listing.latency_ms)

            self.store.queue(listing)

            payload = listing.to_dict()
            self.feed.appendleft(payload)
            self.bus.publish("listing", payload)

            for notifier in self.notifiers:
                notifier.notify(listing)

        if hits:
            await self.store.flush()
            self.bus.publish("stats", self.stats())

        return hits

    def _is_excluded(self, title: str) -> bool:
        words = self.config.filters.exclude_words
        if not words:
            return False
        normalized = normalize(title)
        return any(normalize(word) in normalized for word in words)

    def _remember(self, item_id: str) -> None:
        """Ajoute au cache de dédup, en le gardant borné (FIFO)."""
        self._seen.add(item_id)
        self._seen_order.append(item_id)
        limit = self.config.storage.seen_cache_size
        while len(self._seen_order) > limit:
            self._seen.discard(self._seen_order.popleft())

    # ── Entretien ─────────────────────────────────────────────────────────
    async def _maintenance_loop(self) -> None:
        """Flush périodique, purge, et remontée progressive du débit."""
        try:
            while self._running:
                await asyncio.sleep(30)
                await self.store.flush()

                # Après une accalmie, on regagne du débit petit à petit.
                if (
                    self._bucket.rate < self._base_rate
                    and time.monotonic() > self._throttle_until + 60
                ):
                    restored = min(self._base_rate, self._bucket.rate * 1.15)
                    self._bucket.set_rate(restored)
                    log.info("débit global remonté à %.2f req/s", restored)

                self.bus.publish("stats", self.stats())
        except asyncio.CancelledError:
            pass

    # ── Pilotage à chaud ──────────────────────────────────────────────────
    def add_keyword(self, keyword: str) -> bool:
        keyword = keyword.strip()
        if not keyword or keyword in self.config.keywords:
            return False
        self.config.keywords.append(keyword)
        self.matcher.rules.append(
            Rule.compile(
                keyword,
                min_price=self.config.filters.min_price,
                max_price=self.config.filters.max_price,
            )
        )
        self.matcher.reindex()
        log.info("keyword ajouté: %s", keyword)
        return True

    def remove_keyword(self, keyword: str) -> bool:
        if keyword not in self.config.keywords:
            return False
        self.config.keywords.remove(keyword)
        self.matcher.rules = [
            rule for rule in self.matcher.rules if rule.keyword != keyword
        ]
        self.matcher.reindex()
        log.info("keyword retiré: %s", keyword)
        return True

    def set_source_paused(self, query: str, paused: bool) -> bool:
        state = self._sources.get(query)
        if state is None:
            return False
        state.paused = paused
        return True

    async def add_source(self, query: str, **kwargs: Any) -> bool:
        """Ajoute une source et démarre sa boucle immédiatement."""
        query = query.strip()
        if not query or query in self._sources:
            return False
        source = SourceConfig(query=query, **kwargs)
        state = SourceState(config=source, interval=self._interval_for(source))
        self._sources[query] = state
        self.config.sources.append(source)
        if self._running:
            self._tasks.append(
                asyncio.create_task(
                    self._source_loop(state, 0.0), name=f"source:{query}"
                )
            )
        log.info("source ajoutée: %s", query)
        return True

    # ── Métriques ─────────────────────────────────────────────────────────
    def stats(self) -> dict[str, Any]:
        uptime = time.time() - self._started_at if self._started_at else 0.0
        latencies = sorted(self._latencies)

        def percentile(p: float) -> int:
            if not latencies:
                return 0
            index = min(len(latencies) - 1, int(len(latencies) * p))
            return latencies[index]

        return {
            "running": self._running,
            "uptime_seconds": round(uptime),
            "sources": len([s for s in self._sources.values() if s.config.enabled]),
            "keywords": len(self.config.keywords),
            "total_polls": self.total_polls,
            "total_items_seen": self.total_items_seen,
            "total_hits": self.total_hits,
            "seen_cache": len(self._seen),
            "polls_per_minute": round(self.total_polls / uptime * 60, 1) if uptime else 0,
            "rate_limit": round(self._bucket.rate, 2),
            "throttled": time.monotonic() < self._throttle_until,
            "latency_p50_ms": percentile(0.50),
            "latency_p95_ms": percentile(0.95),
            "notifiers": {n.name: n.stats() for n in self.notifiers},
        }

    def sources_state(self) -> list[dict[str, Any]]:
        return [state.to_dict() for state in self._sources.values()]
