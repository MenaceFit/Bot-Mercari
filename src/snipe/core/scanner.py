"""Le scanner : là où tout se rejoint.

    Scheduler → Source workers → Normalizer → Dedup → Filter → Queue → Notifiers

Trois invariants tiennent la latence :

1. **Le scanner n'attend jamais une notification.** `hub.dispatch()` est
   synchrone et non bloquant ; les workers de notification vivent leur vie.
2. **Le scanner n'attend jamais le disque.** Les écritures SQLite sont
   groupées et exécutées dans un thread.
3. **Une source en panne n'arrête pas les autres.** Chaque worker attrape ses
   propres exceptions, et un échec répété espace la source au lieu de la
   faire disparaître.

La détection de trou mérite une explication. Les résultats arrivent triés du
plus récent au plus ancien, et une page est plafonnée. Si l'annonce la PLUS
ANCIENNE d'une page est encore plus récente que tout ce qu'on avait déjà vu
sur cette requête, c'est que la page ne remonte pas jusqu'au passage
précédent : il manque des annonces entre les deux. On pagine alors pour
combler — et on le signale au planificateur, qui en tirera les conséquences
sur la forme des requêtes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from ..notifications.base import NotificationHub
from ..sources.base import (
    BaseSource,
    Listing,
    SearchQuery,
    SearchResult,
    SourceError,
)
from ..storage.sqlite import Storage
from .deduplicator import Deduplicator
from .filters import FilterEngine
from .keywords import Keyword
from .metrics import Metrics
from .planner import QueryPlanner
from .scheduler import Scheduler

log = logging.getLogger(__name__)


@dataclass
class QueryState:
    """Ce qu'on sait d'une requête donnée sur une source donnée."""

    source: str
    query: str
    #: Publication la plus récente déjà vue. Repère de la détection de trou.
    watermark: float = 0.0
    #: Mots-clés que cette requête sert. Une annonce qu'elle ramène n'est
    #: confrontée qu'à ceux-là — voir FilterEngine.match(allowed=...).
    keywords: frozenset[str] = field(default_factory=frozenset)
    warmed_up: bool = False
    gaps: int = 0
    catchups: int = 0

    @property
    def key(self) -> str:
        return f"{self.source}::{self.query}"


@dataclass
class Scanner:
    """Orchestre sources, planification, filtrage et diffusion."""

    sources: dict[str, BaseSource]
    keywords: list[Keyword]
    filters: FilterEngine
    hub: NotificationHub
    storage: Storage
    scheduler: Scheduler
    metrics: Metrics = field(default_factory=Metrics)
    dedup: Deduplicator = field(default_factory=Deduplicator)
    planners: dict[str, QueryPlanner] = field(default_factory=dict)

    warmup: bool = True
    max_catchup_pages: int = 3
    page_size: int = 60
    buy_url_builder: Any = None
    exclude_text: str = ""

    states: dict[str, QueryState] = field(default_factory=dict)
    _running: bool = False
    _tasks: list[asyncio.Task] = field(default_factory=list)
    #: Pause globale après un 429 : la limite est par adresse IP, pas par
    #: source. Ralentir une seule source ne servirait à rien.
    _throttle_until: float = 0.0

    # ── Cycle de vie ──────────────────────────────────────────────────────
    async def start(self) -> None:
        if self._running:
            return
        self._running = True

        seen = await self.storage.load_seen()
        self.dedup.prime(seen)
        log.info("déduplication amorcée : %d annonces connues", len(seen))

        for name, source in self.sources.items():
            self.metrics.source(name, getattr(source, "verified", True))
            if not getattr(source, "verified", True):
                log.warning(
                    "source « %s » : non vérifiée en conditions réelles depuis "
                    "l'environnement de développement — vérifie ses premiers "
                    "résultats avant de lui faire confiance",
                    name,
                )

        self.replan()
        self._tasks.append(asyncio.create_task(self._loop(), name="scanner"))
        self._tasks.append(asyncio.create_task(self._maintenance(), name="maintenance"))

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self.storage.flush()

    # ── Planification ─────────────────────────────────────────────────────
    def planner(self, source: str) -> QueryPlanner:
        planner = self.planners.get(source)
        if planner is None:
            planner = QueryPlanner()
            self.planners[source] = planner
        return planner

    def replan(self) -> dict[str, Any]:
        """Recalcule les requêtes de chaque source et resynchronise le plan."""
        wanted: list[tuple[str, str, str]] = []
        for name in self.sources:
            for planned in self.planner(name).plan(self.keywords, source=name):
                wanted.append((name, planned.text, planned.priority))
                key = f"{name}::{planned.text}"
                state = self.states.setdefault(
                    key, QueryState(source=name, query=planned.text)
                )
                # Le plan peut changer (dégroupage) : la liste des mots-clés
                # servis doit suivre, sinon une requête devenue précise
                # continuerait d'accepter les mots-clés de l'ancienne.
                state.keywords = frozenset(planned.keywords)

        added, removed = self.scheduler.sync(wanted)
        report = self.scheduler.report()
        log.info(
            "plan : %d requêtes sur %d source(s) pour %d mot(s)-clé(s)"
            "%s",
            len(wanted), len(self.sources), len(self.keywords),
            f" — budget saturé, cadence réelle {report['effective_interval']}s"
            if report.get("saturated") else "",
        )
        if added or removed:
            log.debug("plan modifié : +%d / -%d requêtes", added, removed)
        return report

    # ── Boucle principale ─────────────────────────────────────────────────
    async def _loop(self) -> None:
        try:
            while self._running:
                now = time.monotonic()
                if now < self._throttle_until:
                    await asyncio.sleep(self._throttle_until - now)
                    continue

                due = self.scheduler.due(now)
                if not due:
                    await asyncio.sleep(self.scheduler.next_deadline(now))
                    continue

                # Les requêtes échues partent EN PARALLÈLE. Les traiter en
                # série ferait payer à la dernière la somme des latences des
                # précédentes — exactement ce qu'on cherche à éviter.
                await asyncio.gather(
                    *(self._run_task(task) for task in due),
                    return_exceptions=True,
                )
                for task in due:
                    task.schedule_next(time.monotonic(), self.scheduler.jitter)
        except asyncio.CancelledError:
            pass

    async def _run_task(self, task) -> None:
        source = self.sources.get(task.source)
        if source is None:
            return
        state = self.states.setdefault(
            task.key, QueryState(source=task.source, query=task.query)
        )
        stats = self.metrics.source(task.source)

        try:
            result = await source.search(self._query(task.query))
        except SourceError as exc:
            self._handle_error(task, exc, stats)
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Une source qui lève autre chose qu'un SourceError est un bug,
            # pas une panne réseau — mais elle ne doit pas non plus arrêter
            # le scanner.
            log.exception("source %s : exception inattendue", task.source)
            stats.record_error(f"{type(exc).__name__}: {exc}")
            return

        stats.record_ok(result.latency_ms, len(result))
        self.metrics.network.add(result.latency_ms)

        listings = list(result.listings)
        if self._has_gap(state, listings) and result.cursor:
            listings.extend(await self._catch_up(source, task.query, state, result))

        self._raise_watermark(state, listings)
        await self._process(task, state, listings)

    def _query(self, text: str) -> SearchQuery:
        return SearchQuery(
            text=text,
            limit=self.page_size,
            min_price=self.filters.globals_.min_price,
            max_price=self.filters.globals_.max_price,
            exclude_text=self.exclude_text,
        )

    # ── Détection de trou ─────────────────────────────────────────────────
    def _has_gap(self, state: QueryState, listings: list[Listing]) -> bool:
        """La page remonte-t-elle jusqu'au passage précédent ?

        Le critère naïf — « la page est entièrement inédite » — rate le cas
        le plus fréquent : le cache de déduplication est global, il suffit
        qu'une autre requête ait croisé une annonce pour masquer un trou réel.
        """
        if not listings or not state.warmed_up:
            return False

        dated = [item.published_at for item in listings if item.published_at]
        if len(dated) == len(listings) and state.watermark:
            return min(dated) > state.watermark

        # Sans date exploitable, on retombe sur le critère grossier. Le biais
        # penche vers la pagination : se tromper coûte une requête, l'inverse
        # coûte une annonce ratée.
        return all(not self.dedup.seen(item.key) for item in listings)

    @staticmethod
    def _raise_watermark(state: QueryState, listings: list[Listing]) -> None:
        newest = max((item.published_at for item in listings), default=0.0)
        if newest > state.watermark:
            state.watermark = newest

    async def _catch_up(
        self, source: BaseSource, query: str, state: QueryState, first: SearchResult
    ) -> list[Listing]:
        """Remonte les pages suivantes jusqu'à retrouver du connu."""
        state.gaps += 1
        planner = self.planner(state.source)
        if planner.record_gap(query):
            # Le plan change : la requête était trop large pour la cadence.
            self.replan()

        log.warning(
            "trou sur « %s » (%s) : la page ne remonte pas au scan précédent, "
            "rattrapage",
            query, state.source,
        )

        recovered: list[Listing] = []
        cursor = first.cursor
        for _ in range(self.max_catchup_pages):
            if not cursor:
                break
            try:
                page = await source.search(
                    SearchQuery(text=query, limit=self.page_size, cursor=cursor)
                )
            except SourceError as exc:
                log.warning("rattrapage interrompu sur « %s » : %s", query, exc)
                break
            state.catchups += 1
            recovered.extend(page.listings)
            if any(self.dedup.seen(item.key) for item in page.listings):
                break        # continuité rétablie
            cursor = page.cursor

        log.info("rattrapage « %s » : %d annonces récupérées", query, len(recovered))
        return recovered

    # ── Traitement ────────────────────────────────────────────────────────
    async def _process(self, task, state: QueryState, listings: list[Listing]) -> None:
        if not listings:
            return

        fresh = [item for item in listings if self.dedup.add(item.key)]
        duplicates = len(listings) - len(fresh)
        if duplicates:
            self.metrics.duplicates.inc(duplicates)

        planner = self.planner(state.source)

        # Premier passage : on mémorise sans notifier. Sinon tout ce qui est
        # déjà en ligne partirait en notification au démarrage.
        if not state.warmed_up:
            state.warmed_up = True
            if self.warmup and fresh:
                await self.storage.mark_seen(item.key for item in fresh)
                log.info(
                    "warmup « %s » : %d annonces mémorisées, aucune notification",
                    task.query, len(fresh),
                )
                planner.record_page(task.query, len(listings), 0)
                return

        now = time.time()
        hits = 0
        for listing in fresh:
            matched = self.filters.match(
                listing, now=now, allowed=state.keywords or None
            )
            if not matched:
                continue
            listing.matched_at = time.time()
            listing.keywords = matched
            listing.keyword = matched[0]
            if self.buy_url_builder is not None:
                listing.buy_url = self.buy_url_builder(listing)

            self.metrics.new_listings.inc()
            self.metrics.pipeline.add(listing.pipeline_ms)
            if listing.detection_ms:
                self.metrics.detection.add(listing.detection_ms)

            self.storage.queue(listing)
            # Non bloquant : le scanner repart immédiatement.
            self.hub.dispatch(listing)
            hits += 1

        planner.record_page(task.query, len(listings), hits)

    # ── Erreurs ───────────────────────────────────────────────────────────
    def _handle_error(self, task, exc: SourceError, stats) -> None:
        stats.record_error(str(exc), rate_limited=exc.is_rate_limit)

        if exc.is_rate_limit:
            pause = exc.retry_after or 5.0
            self._throttle_until = time.monotonic() + pause
            # Le budget global baisse : la limite est par IP, la répartir
            # entre les sources ne changerait rien.
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
            task.interval = min(task.interval * 2, 300.0)
        else:
            log.warning("erreur sur « %s » (%s) : %s", task.query, task.source, exc)

        if stats.consecutive_errors >= 3:
            task.interval = min(task.interval * 1.5, 300.0)

    # ── Entretien ─────────────────────────────────────────────────────────
    async def _maintenance(self) -> None:
        base_budget = self.scheduler.budget_per_second
        try:
            while self._running:
                await asyncio.sleep(30)
                await self.storage.flush()

                # Rendement : dégroupe les requêtes qui gaspillent le budget.
                changed = False
                for planner in self.planners.values():
                    if planner.review_yield():
                        changed = True
                if changed:
                    self.replan()

                # Après une accalmie, on regagne du débit progressivement.
                if (
                    self.scheduler.budget_per_second < base_budget
                    and time.monotonic() > self._throttle_until + 60
                ):
                    self.scheduler.budget_per_second = min(
                        base_budget, self.scheduler.budget_per_second * 1.15
                    )
                    self.scheduler.allocate()
        except asyncio.CancelledError:
            pass

    # ── Introspection ─────────────────────────────────────────────────────
    async def health_check(self) -> dict[str, Any]:
        """Teste chaque source indépendamment. Une panne n'en cache pas une autre."""
        report = {}
        for name, source in self.sources.items():
            try:
                health = await source.health_check()
            except Exception as exc:
                health = type("H", (), {
                    "ok": False, "detail": f"{type(exc).__name__}: {exc}",
                    "latency_ms": 0,
                })()
            stats = self.metrics.source(name)
            was = stats.healthy
            stats.healthy = health.ok
            if was is True and not health.ok:
                log.error("source %s : PANNE — %s", name, health.detail)
            elif was is False and health.ok:
                log.info("source %s : RÉTABLIE", name)
            report[name] = {
                "ok": health.ok,
                "detail": health.detail,
                "latency_ms": health.latency_ms,
                "verified": getattr(source, "verified", True),
            }
        return report

    def snapshot(self) -> dict[str, Any]:
        return {
            "metrics": self.metrics.snapshot(),
            "scheduler": self.scheduler.report(),
            "dedup": self.dedup.report(),
            "filters": self.filters.report(),
            "notifications": self.hub.report(),
            "planners": {
                name: planner.report() for name, planner in self.planners.items()
            },
            "queries": [
                {
                    "source": state.source,
                    "query": state.query,
                    "gaps": state.gaps,
                    "catchups": state.catchups,
                    "warmed_up": state.warmed_up,
                }
                for state in self.states.values()
            ],
        }
