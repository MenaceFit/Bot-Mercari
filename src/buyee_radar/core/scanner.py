"""Le scanner : ingestion → normalisation → dédup → filtrage → scoring → bus.

Quatre invariants gouvernent tout le module.

1. **Le scanner n'attend jamais une notification.** `dispatcher.dispatch()`
   et `bus.publish()` sont synchrones et non bloquants.
2. **Le scanner n'attend jamais le disque.** Les écritures SQLite sont
   groupées et exécutées dans un thread.
3. **Une source qui tombe n'arrête pas les autres.** Chaque adapter a son
   circuit breaker ; ouvert, il rend la main instantanément.
4. **Les requêtes échues partent en parallèle.** Les enchaîner ferait payer
   à la dernière la somme des latences des précédentes.

Détection incrémentale
----------------------
Les résultats arrivent du plus récent au plus ancien, et une page est
plafonnée. Si l'annonce la PLUS ANCIENNE d'une page est encore plus récente
que tout ce qu'on avait déjà vu sur cette requête, la page ne remonte pas
jusqu'au passage précédent : il manque des annonces entre les deux. On
pagine alors — et seulement alors. Jamais de balayage systématique.

Quand la source ne date pas ses annonces (fréquent en HTML), on retombe sur
un repère par identifiants : la page est-elle entièrement inédite ?
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from ..adapters.base import (
    AdapterError,
    Listing,
    MarketplaceAdapter,
    SearchQuery,
    SearchResult,
    SupportLevel,
)
from ..notifications.dispatcher import NotificationHub
from ..storage.database import Database
from .circuit_breaker import CircuitBreaker
from .currency import CurrencyConverter
from .deduplicator import Deduplicator
from .event_bus import EventBus
from .keywords import Keyword
from .matcher import FilterEngine
from .metrics import Metrics
from .query_optimizer import QueryOptimizer
from .scheduler import Scheduler
from .scoring import ScoringEngine

log = logging.getLogger(__name__)


@dataclass
class Checkpoint:
    """Où en est une requête donnée sur une source donnée."""

    source: str
    query: str
    #: Publication la plus récente déjà vue. Repère principal.
    last_seen_timestamp: float = 0.0
    #: Identifiant le plus récent vu. Repère de repli quand la source ne
    #: date pas ses annonces — sans supposer que les ids sont monotoniques,
    #: on s'en sert seulement pour tester l'appartenance.
    last_seen_ids: set[str] = field(default_factory=set)
    cursor: str = ""
    keywords: frozenset[str] = field(default_factory=frozenset)
    warmed_up: bool = False
    gaps: int = 0
    catchups: int = 0
    items_seen: int = 0

    @property
    def key(self) -> str:
        return f"{self.source}::{self.query}"

    def remember(self, listings: list[Listing]) -> None:
        newest = max((item.created_at for item in listings), default=0.0)
        if newest > self.last_seen_timestamp:
            self.last_seen_timestamp = newest
        self.last_seen_ids = {item.key for item in listings[:40]}


@dataclass
class Scanner:
    """Orchestre adapters, planification, filtrage, scoring et diffusion."""

    adapters: dict[str, MarketplaceAdapter]
    keywords: list[Keyword]
    filters: FilterEngine
    hub: NotificationHub
    database: Database
    scheduler: Scheduler
    bus: EventBus
    scoring: ScoringEngine = field(default_factory=ScoringEngine)
    currency: CurrencyConverter = field(default_factory=CurrencyConverter)
    metrics: Metrics = field(default_factory=Metrics)
    dedup: Deduplicator = field(default_factory=Deduplicator)
    optimizers: dict[str, QueryOptimizer] = field(default_factory=dict)
    breakers: dict[str, CircuitBreaker] = field(default_factory=dict)

    warmup: bool = True
    #: Cadence de vidage de la file d'écriture. Le dashboard lit la base
    #: pour son état initial et ses analytics : une seule purge toutes les
    #: 30 s le laisserait vide une demi-minute après chaque ouverture,
    #: alors que la détection, elle, a bien eu lieu.
    flush_every: float = 5.0
    max_catchup_pages: int = 3
    page_size: int = 60
    buy_url_builder: Any = None
    #: Seuils de notification (§24). Une annonce sous le minimum reste
    #: visible au dashboard mais ne réveille personne.
    notify_min_score: int = 0

    checkpoints: dict[str, Checkpoint] = field(default_factory=dict)
    paused: bool = False
    _running: bool = False
    _tasks: list[asyncio.Task] = field(default_factory=list)
    _throttle_until: float = 0.0
    _base_budget: float = 0.0

    # ── Cycle de vie ──────────────────────────────────────────────────────
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._base_budget = self.scheduler.budget_per_second

        seen = await self.database.load_seen()
        self.dedup.prime(seen)
        log.info("déduplication amorcée : %d annonces connues", len(seen))

        for name, adapter in self.adapters.items():
            await adapter.start()
            self.breakers.setdefault(name, CircuitBreaker(name=name))
            support = getattr(adapter, "support", SupportLevel.VERIFIED)
            self.metrics.source(name, support is SupportLevel.VERIFIED)
            if support is not SupportLevel.VERIFIED:
                log.warning(
                    "source « %s » — %s : %s",
                    name, support.value, getattr(adapter, "support_note", "")[:180],
                )

        self.replan()
        self._tasks.append(asyncio.create_task(self._loop(), name="scanner"))
        self._tasks.append(asyncio.create_task(self._maintenance(), name="maintenance"))
        self._tasks.append(asyncio.create_task(self._flush_loop(), name="flush"))
        self.bus.publish("scanner_started", {"sources": list(self.adapters)})

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self.database.flush()
        for adapter in self.adapters.values():
            await adapter.stop()
        self.bus.publish("scanner_stopped", {})

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        self.bus.publish("scanner_paused", {"paused": paused})
        log.info("scanner %s", "en pause" if paused else "repris")

    # ── Planification ─────────────────────────────────────────────────────
    def optimizer(self, source: str) -> QueryOptimizer:
        opt = self.optimizers.get(source)
        if opt is None:
            opt = QueryOptimizer()
            self.optimizers[source] = opt
        return opt

    def replan(self) -> dict[str, Any]:
        """Recalcule les requêtes et resynchronise l'ordonnanceur."""
        wanted: list[tuple[str, str, str]] = []
        for name, adapter in self.adapters.items():
            if getattr(adapter, "support", SupportLevel.VERIFIED) is SupportLevel.UNSUPPORTED:
                continue
            for planned in self.optimizer(name).plan(self.keywords, source=name):
                wanted.append((name, planned.text, planned.priority))
                checkpoint = self.checkpoints.setdefault(
                    f"{name}::{planned.text}",
                    Checkpoint(source=name, query=planned.text),
                )
                # Le plan peut changer (dégroupage) : les mots-clés servis
                # doivent suivre, sinon une requête devenue précise
                # accepterait encore ceux de l'ancienne.
                checkpoint.keywords = frozenset(planned.keywords)

        self.scheduler.sync(wanted)
        report = self.scheduler.report()
        log.info(
            "plan : %d requêtes sur %d source(s) pour %d mot(s)-clé(s)%s",
            len(wanted), len(self.adapters), len(self.keywords),
            f" — budget saturé, cadence réelle {report['effective_interval']}s"
            if report.get("saturated") else "",
        )
        self.bus.publish("plan_changed", {"queries": len(wanted), **report})
        return report

    def set_keywords(self, keywords: list[Keyword]) -> None:
        self.keywords = keywords
        self.filters.set_keywords(keywords)
        self.replan()

    # ── Boucle principale ─────────────────────────────────────────────────
    async def _loop(self) -> None:
        try:
            while self._running:
                if self.paused:
                    await asyncio.sleep(0.25)
                    continue

                now = time.monotonic()
                if now < self._throttle_until:
                    await asyncio.sleep(self._throttle_until - now)
                    continue

                due = self.scheduler.due(now)
                if not due:
                    await asyncio.sleep(self.scheduler.next_deadline(now))
                    continue

                await asyncio.gather(
                    *(self._run_task(task) for task in due),
                    return_exceptions=True,
                )
                for task in due:
                    task.schedule_next(time.monotonic(), self.scheduler.jitter)
        except asyncio.CancelledError:
            pass

    async def _run_task(self, task) -> None:
        adapter = self.adapters.get(task.source)
        if adapter is None:
            return

        breaker = self.breakers.setdefault(
            task.source, CircuitBreaker(name=task.source)
        )
        if not breaker.allows():
            # Circuit ouvert : on rend la main immédiatement. Une source
            # coupée ne doit rien coûter, pas même un timeout.
            return

        checkpoint = self.checkpoints.setdefault(
            task.key, Checkpoint(source=task.source, query=task.query)
        )
        stats = self.metrics.source(task.source)

        try:
            result = await adapter.fetch_latest(self._query(task.query))
        except AdapterError as exc:
            self._handle_error(task, breaker, stats, exc)
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Un adapter qui lève autre chose est un bug, pas une panne —
            # mais il ne doit pas non plus arrêter le scanner.
            log.exception("adapter %s : exception inattendue", task.source)
            stats.record_error(f"{type(exc).__name__}: {exc}")
            breaker.record_failure(str(exc))
            return

        if not result.ok:
            stats.record_error(result.error)
            breaker.record_failure(result.error)
            self.bus.publish(
                "source_error", {"source": task.source, "error": result.error}
            )
            return

        breaker.record_success()
        stats.record_ok(result.latency_ms, len(result))
        self.metrics.network.add(result.latency_ms)

        listings = list(result.listings)
        if self._has_gap(checkpoint, listings) and result.cursor:
            listings.extend(await self._catch_up(adapter, task, checkpoint, result))

        checkpoint.remember(listings)
        checkpoint.items_seen += len(listings)
        await self._process(task, checkpoint, listings)

    def _query(self, text: str) -> SearchQuery:
        return SearchQuery(
            text=text,
            limit=self.page_size,
            min_price=self.filters.globals_.min_price,
            max_price=self.filters.globals_.max_price,
        )

    # ── Détection incrémentale ────────────────────────────────────────────
    def _has_gap(self, checkpoint: Checkpoint, listings: list[Listing]) -> bool:
        if not listings or not checkpoint.warmed_up:
            return False

        dated = [item.created_at for item in listings if item.created_at]
        if len(dated) == len(listings) and checkpoint.last_seen_timestamp:
            return min(dated) > checkpoint.last_seen_timestamp

        # Sans date fiable : la page est-elle entièrement inédite ? Le biais
        # penche vers la pagination — se tromper coûte une requête, l'inverse
        # coûte une annonce ratée.
        return all(not self.dedup.seen(item.key) for item in listings)

    async def _catch_up(
        self, adapter, task, checkpoint: Checkpoint, first: SearchResult
    ) -> list[Listing]:
        checkpoint.gaps += 1
        if self.optimizer(task.source).record_gap(task.query):
            self.replan()

        log.warning(
            "trou sur « %s » (%s) : la page ne remonte pas au scan précédent",
            task.query, task.source,
        )
        self.bus.publish("gap_detected", {"source": task.source, "query": task.query})

        recovered: list[Listing] = []
        cursor = first.cursor
        for _ in range(self.max_catchup_pages):
            if not cursor:
                break
            try:
                page = await adapter.search(
                    SearchQuery(text=task.query, limit=self.page_size, cursor=cursor)
                )
            except AdapterError as exc:
                log.warning("rattrapage interrompu : %s", exc)
                break
            if not page.ok:
                break
            checkpoint.catchups += 1
            recovered.extend(page.listings)
            if any(self.dedup.seen(item.key) for item in page.listings):
                break        # continuité rétablie
            cursor = page.cursor

        log.info("rattrapage « %s » : %d annonces", task.query, len(recovered))
        return recovered

    # ── Traitement ────────────────────────────────────────────────────────
    async def _process(self, task, checkpoint: Checkpoint, listings: list[Listing]) -> None:
        if not listings:
            return

        fresh = [item for item in listings if self.dedup.add(item.key)]
        duplicates = len(listings) - len(fresh)
        if duplicates:
            self.metrics.duplicates.inc(duplicates)

        optimizer = self.optimizer(task.source)

        if not checkpoint.warmed_up:
            checkpoint.warmed_up = True
            if self.warmup and fresh:
                await self.database.mark_seen(item.key for item in fresh)
                log.info(
                    "warmup « %s » : %d annonces mémorisées, aucune notification",
                    task.query, len(fresh),
                )
                optimizer.record_page(task.query, len(listings), 0)
                return

        now = time.time()
        hits = 0
        for listing in fresh:
            matched = self.filters.match(
                listing, now=now, allowed=checkpoint.keywords or None
            )
            if not matched:
                continue

            listing.matched_at = time.time()
            listing.keywords = matched
            listing.keyword = matched[0]

            breakdown = self.scoring.score(listing)
            listing.score = breakdown.total
            listing.tier = breakdown.tier
            self.scoring.observe(listing)

            listing.price_eur = self.currency.convert(listing.price)
            if self.buy_url_builder is not None:
                listing.buy_url = self.buy_url_builder(listing)

            self.metrics.new_listings.inc()
            self.metrics.pipeline.add(listing.pipeline_ms)
            if listing.latency_ms:
                self.metrics.detection.add(listing.latency_ms)

            payload = {**listing.to_dict(), "score_parts": breakdown.parts}
            self.database.queue(listing)
            # Les deux sont synchrones et non bloquants : le scanner repart.
            self.bus.publish("listing", payload)
            if listing.score >= self.notify_min_score:
                self.hub.dispatch(listing)
            hits += 1

        optimizer.record_page(task.query, len(listings), hits)

    # ── Erreurs ───────────────────────────────────────────────────────────
    def _handle_error(self, task, breaker: CircuitBreaker, stats, exc: AdapterError) -> None:
        stats.record_error(str(exc), rate_limited=exc.is_rate_limit)
        breaker.record_failure(str(exc))

        if exc.is_rate_limit:
            pause = exc.retry_after or 5.0
            self._throttle_until = time.monotonic() + pause
            # La limite est par adresse IP : on baisse le budget GLOBAL.
            self.scheduler.budget_per_second = max(
                0.5, self.scheduler.budget_per_second * 0.7
            )
            self.scheduler.allocate()
            log.warning(
                "429 sur « %s » — pause %.1f s, budget ramené à %.2f req/s",
                task.query, pause, self.scheduler.budget_per_second,
            )
        elif exc.is_blocked:
            log.error("accès refusé sur %s : %s", task.source, exc)

        self.bus.publish(
            "source_error", {"source": task.source, "error": str(exc)[:200]}
        )

    async def _flush_loop(self) -> None:
        """Vide la file d'écriture à cadence rapprochée.

        Séparé de l'entretien : celui-ci fait des choses coûteuses (revue des
        rendements, taux de change) qu'on ne veut pas exécuter toutes les
        cinq secondes, alors que l'écriture est un simple `executemany`
        exécuté hors de la boucle d'événements.
        """
        try:
            while self._running:
                await asyncio.sleep(max(1.0, self.flush_every))
                if self.database.pending:
                    await self.database.flush()
        except asyncio.CancelledError:
            pass

    # ── Entretien ─────────────────────────────────────────────────────────
    async def _maintenance(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(30)
                await self.database.flush()
                await self.currency.refresh()

                changed = any(opt.review_yield() for opt in self.optimizers.values())
                if changed:
                    self.replan()

                if (
                    self.scheduler.budget_per_second < self._base_budget
                    and time.monotonic() > self._throttle_until + 60
                ):
                    self.scheduler.budget_per_second = min(
                        self._base_budget, self.scheduler.budget_per_second * 1.15
                    )
                    self.scheduler.allocate()

                self.bus.publish("metrics", self.snapshot()["metrics"])
        except asyncio.CancelledError:
            pass

    # ── Introspection ─────────────────────────────────────────────────────
    async def health_check(self) -> list[dict[str, Any]]:
        """Teste chaque source indépendamment. Une panne n'en cache pas une autre."""
        reports = []
        for name, adapter in self.adapters.items():
            try:
                health = await adapter.health_check()
                report = health.to_dict()
            except Exception as exc:
                report = {
                    "source": name, "ok": False,
                    "detail": f"{type(exc).__name__}: {exc}",
                    "latency_ms": 0, "support": "unknown",
                }
            stats = self.metrics.source(name)
            was, stats.healthy = stats.healthy, report["ok"]
            if was is True and not report["ok"]:
                log.error("source %s : PANNE — %s", name, report["detail"])
            elif was is False and report["ok"]:
                log.info("source %s : RÉTABLIE", name)
            report["label"] = getattr(adapter, "label", name)
            report["breaker"] = self.breakers.get(
                name, CircuitBreaker(name=name)
            ).to_dict()
            reports.append(report)
        self.bus.publish("health", reports)
        return reports

    def snapshot(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "paused": self.paused,
            "metrics": self.metrics.snapshot(),
            "scheduler": self.scheduler.report(),
            "dedup": self.dedup.report(),
            "filters": self.filters.report(),
            "notifications": self.hub.report(),
            "scoring": self.scoring.stats(),
            "currency": self.currency.status(),
            "bus": self.bus.stats(),
            "breakers": [b.to_dict() for b in self.breakers.values()],
            "optimizers": {
                name: opt.report() for name, opt in self.optimizers.items()
            },
            "checkpoints": [
                {
                    "source": c.source, "query": c.query, "gaps": c.gaps,
                    "catchups": c.catchups, "items_seen": c.items_seen,
                    "warmed_up": c.warmed_up,
                    "keywords": sorted(c.keywords),
                }
                for c in self.checkpoints.values()
            ],
        }
