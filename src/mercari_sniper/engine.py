"""Moteur de détection : ordonnancement, déduplication, dispatch.

Modèle d'exécution
------------------
Une tâche asyncio par source, chacune bouclant à son propre rythme et
partageant un token bucket global.

Quatre mécanismes garantissent qu'on ne rate pas d'annonce :

1. **Ordonnancement sans dérive** — l'échéance suivante se calcule depuis la
   précédente (`deadline += interval`), pas depuis la fin du travail.

2. **Détection de débordement** — une source ne voit que les `page_size`
   annonces les plus récentes. Si *toutes* les annonces d'une page sont
   inédites, c'est qu'il y en a probablement eu davantage entre deux scans :
   on remonte les pages suivantes jusqu'à retrouver du connu, et on resserre
   l'intervalle de cette source.

3. **Tampon réversible** — la déduplication seule est irréversible : une
   annonce écartée faute de keyword correspondant ne pouvait plus jamais être
   réévaluée. Toutes les annonces brutes passent donc par un `RecentBuffer`,
   que l'on repasse quand un keyword est ajouté.

4. **Cadence adaptative** — l'intervalle de chaque source suit son débit réel
   d'annonces, au lieu d'être figé. Une source saturée accélère, une source
   morte s'espace et rend son budget aux autres.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Any

from .backends.base import BackendError, SearchBackend, SearchQuery
from .buffer import RecentBuffer
from .buyee import buyee_url
from .config import Config, SourceConfig
from .events import EventBus
from . import noise
from .matching import (
    Matcher,
    Rule,
    contains_term,
    covers,
    normalize,
    rarity_of,
)
from .models import Listing
from .ratelimit import TokenBucket
from .store import Store

log = logging.getLogger(__name__)

# Au-delà de ce taux de remplissage, la source frôle le débordement.
_SATURATION_RATIO = 0.35


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
    new_items: int = 0
    overflows: int = 0
    catchup_pages: int = 0
    fill_ratio: float = 0.0
    # Date de publication la plus récente déjà vue par cette source. C'est
    # le repère qui permet de savoir si un trou s'est formé entre deux scans.
    watermark: int = 0
    gaps: int = 0
    items_seen: int = 0
    split_from: str = ""
    last_poll: float = 0.0
    last_error: str = ""
    last_duration_ms: int = 0
    paused: bool = False
    task: asyncio.Task | None = field(default=None, repr=False, compare=False)

    @property
    def yield_ratio(self) -> float:
        """Trouvailles par annonce examinée — le rendement de la requête.

        Proche de zéro sur une requête trop large : elle brûle du budget
        pour des articles qui ne matcheront jamais.
        """
        return self.hits / self.items_seen if self.items_seen else 0.0

    @property
    def health(self) -> str:
        """État lisible, jamais porté par la couleur seule côté interface."""
        if self.paused:
            return "paused"
        if self.consecutive_errors >= 3:
            return "error"
        if self.consecutive_errors:
            return "degraded"
        if self.fill_ratio >= _SATURATION_RATIO:
            return "saturated"
        if not self.polls:
            return "starting"
        return "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.config.query,
            "health": self.health,
            "enabled": self.config.enabled and not self.paused,
            "paused": self.paused,
            "auto": self.config.auto,
            "interval": round(self.interval, 1),
            "polls": self.polls,
            "errors": self.errors,
            "hits": self.hits,
            "new_items": self.new_items,
            "overflows": self.overflows,
            "gaps": self.gaps,
            "items_seen": self.items_seen,
            "yield_ratio": round(self.yield_ratio, 4),
            "split_from": self.split_from,
            "fill_ratio": round(self.fill_ratio, 3),
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

        self.matcher = Matcher([self._make_rule(k) for k in config.keywords])
        self._exclude_terms: tuple[str, ...] = ()
        self._exclude_keyword = ""
        self.refresh_filters()

        # Annonces écartées, par motif : sans ce compte, un filtre trop
        # strict est indiscernable d'un marché calme.
        self.drops: dict[str, int] = {"bruit": 0, "trop ancienne": 0}

        self._bucket = TokenBucket(config.poll.global_rate_limit)
        self._base_rate = config.poll.global_rate_limit
        self._seen: set[str] = set()
        self._seen_order: deque[str] = deque()
        self._emitted: set[str] = set()
        self._emitted_order: deque[str] = deque()
        self._sources: dict[str, SourceState] = {}
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self._started_at = 0.0
        self._throttle_until = 0.0
        self._save_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

        self.buffer = RecentBuffer(
            window_seconds=config.storage.recent_buffer_seconds,
            max_items=config.storage.recent_buffer_size,
        )

        # Compteurs globaux
        self.total_polls = 0
        self.total_items_seen = 0
        self.total_hits = 0
        self.total_overflows = 0
        self._latencies: deque[int] = deque(maxlen=500)
        self._hit_times: deque[float] = deque(maxlen=5000)
        self._keyword_hits: dict[str, int] = {}

        self.feed: deque[dict[str, Any]] = deque(maxlen=config.server.max_feed)

        for source in config.sources:
            self._sources[source.query] = SourceState(
                config=source, interval=self._interval_for(source)
            )

    # ── Paramétrage ───────────────────────────────────────────────────────
    def _make_rule(self, keyword: str) -> Rule:
        return Rule.compile(
            keyword,
            min_price=self.config.filters.min_price,
            max_price=self.config.filters.max_price,
        )

    def _interval_for(self, source: SourceConfig) -> float:
        if source.interval is not None:
            return max(self.config.poll.min_interval, source.interval)
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
        if not active:
            log.warning(
                "aucune source active — ajoute un keyword depuis le dashboard"
            )

        for index, state in enumerate(active):
            # Décalage initial : évite que toutes les sources partent ensemble.
            delay = (index / max(1, len(active))) * self.config.poll.interval
            self._spawn_source_task(state, delay)

        self._tasks.append(asyncio.create_task(self._maintenance_loop(), name="maint"))

    def _spawn_source_task(self, state: SourceState, delay: float = 0.0) -> None:
        task = asyncio.create_task(
            self._source_loop(state, delay), name=f"source:{state.config.query}"
        )
        state.task = task
        self._tasks.append(task)

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False

        if self._save_task is not None:
            self._save_task.cancel()
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
                if state.paused or not state.config.enabled:
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

    def _query_for(self, state: SourceState, page_token: str = "") -> SearchQuery:
        # Le bruit est écarté par Mercari lui-même : il ne consomme alors ni
        # bande passante, ni — surtout — de place dans la page de résultats.
        # Une page de 120 remplie de parfums, c'est 120 annonces utiles en
        # moins et un trou de plus à rattraper.
        exclude = " ".join(
            part for part in (state.config.exclude_keyword, self._exclude_keyword)
            if part
        ).strip()

        return SearchQuery(
            keyword=state.config.query,
            page_size=state.config.page_size,
            min_price=state.config.min_price or self.config.filters.min_price,
            max_price=state.config.max_price or self.config.filters.max_price,
            exclude_keyword=exclude,
            categories=list(
                state.config.categories or self.config.filters.include_categories
            ),
            page_token=page_token,
        )

    async def _poll_source(self, state: SourceState) -> int:
        now = time.monotonic()
        if now < self._throttle_until:
            await asyncio.sleep(self._throttle_until - now)
        await self._bucket.acquire()

        started = time.perf_counter()
        try:
            page = await self.backend.search(self._query_for(state))
        except BackendError as exc:
            self._handle_error(state, exc)
            return 0

        state.last_duration_ms = int((time.perf_counter() - started) * 1000)
        state.polls += 1
        state.last_poll = time.time()
        state.consecutive_errors = 0
        self.total_polls += 1

        listings = list(page.items)
        new_in_page = sum(1 for item in listings if item.id not in self._seen)

        # Taux de remplissage : proportion de la page qui est inédite.
        ratio = new_in_page / max(1, len(listings)) if listings else 0.0
        # Lissage exponentiel : une salve isolée ne doit pas affoler la cadence.
        state.fill_ratio = 0.7 * state.fill_ratio + 0.3 * ratio

        if state.warmed_up and self._has_gap(state, listings) and page.next_page_token:
            listings.extend(await self._catch_up(state, page.next_page_token))

        self._raise_watermark(state, listings)

        self._adapt_interval(state)
        return await self._process(state, listings)

    def _has_gap(self, state: SourceState, listings: list[Listing]) -> bool:
        """Manque-t-il des annonces entre le scan précédent et celui-ci ?

        Les résultats arrivent triés du plus récent au plus ancien, et une
        page est plafonnée à `page_size`. Si l'annonce la PLUS ANCIENNE de la
        page est encore plus récente que tout ce qu'on avait déjà vu, c'est
        que la page n'est pas remontée jusqu'à notre dernier passage : il
        existe un trou entre les deux, et il faut paginer pour le combler.

        L'ancien critère — « la page entière est inédite » — laissait passer
        le cas le plus fréquent : 119 nouveautés sur 120 ne le déclenchent
        pas, alors que la page est déjà pleine et qu'il en manque forcément
        derrière. C'est cette maille-là qui faisait rater des annonces.
        """
        if not listings:
            return False

        dated = [item.created for item in listings if item.created]
        if len(dated) == len(listings) and state.watermark:
            return min(dated) > state.watermark

        # Sans repère utilisable — page sans dates, ou premier scan après un
        # redémarrage — on retombe sur le critère d'origine : une page
        # entièrement inédite trahit très probablement un trou.
        #
        # Le biais est volontaire. Se tromper dans ce sens coûte une requête
        # de pagination ; se tromper dans l'autre coûte une annonce ratée,
        # c'est-à-dire précisément ce que le bot est censé ne jamais faire.
        return all(item.id not in self._seen for item in listings)

    @staticmethod
    def _raise_watermark(state: SourceState, listings: list[Listing]) -> None:
        newest = max((item.created for item in listings if item.created), default=0)
        if newest > state.watermark:
            state.watermark = newest

    async def _catch_up(self, state: SourceState, token: str) -> list[Listing]:
        """Remonte les pages suivantes jusqu'à retrouver une annonce connue."""
        state.overflows += 1
        state.gaps += 1
        self.total_overflows += 1
        recovered: list[Listing] = []

        log.warning(
            "trou détecté sur '%s' : la page ne remonte pas au scan précédent, "
            "rattrapage en cours",
            state.config.query,
        )

        for _ in range(self.config.poll.max_catchup_pages):
            if not token:
                break
            await self._bucket.acquire()
            try:
                page = await self.backend.search(self._query_for(state, token))
            except BackendError as exc:
                self._handle_error(state, exc)
                break

            state.catchup_pages += 1
            self.total_polls += 1
            recovered.extend(page.items)

            # Dès qu'une annonce connue apparaît, la continuité est rétablie.
            if any(item.id in self._seen for item in page.items):
                break
            token = page.next_page_token

        # Le débordement veut dire qu'on scanne trop lentement pour cette source.
        state.interval = max(self.config.poll.min_interval, state.interval * 0.6)
        log.info(
            "rattrapage '%s' : %d annonces récupérées, intervalle ramené à %.1fs",
            state.config.query,
            len(recovered),
            state.interval,
        )
        return recovered

    def _adapt_interval(self, state: SourceState) -> None:
        """Resserre une source qui frôle le débordement.

        Une source CALME n'est jamais ralentie, contrairement à la version
        précédente. Le raisonnement d'alors — « elle ne rapporte rien, qu'elle
        rende son budget » — est exactement l'inverse de ce qu'attend un
        sniper : une requête précise est silencieuse pendant des heures, puis
        l'annonce rare tombe. La ralentir à 60 s, c'est la détecter jusqu'à
        une minute trop tard, quand elle est déjà vendue.

        Le budget se répartit ailleurs, dans `_allocate_budget()`, qui ne
        ralentit que si la somme des demandes dépasse réellement le débit
        autorisé — et alors pour toutes les sources, pas seulement les calmes.
        """
        if not self.config.poll.adaptive or state.config.interval is not None:
            return
        if state.fill_ratio >= _SATURATION_RATIO:
            state.interval = max(
                self.config.poll.min_interval, state.interval * 0.8
            )

    def _allocate_budget(self) -> dict[str, Any]:
        """Répartit le débit autorisé entre les sources actives.

        Le token bucket seul ne suffit pas : quand la demande dépasse le
        budget, les sources continuent de viser leur intervalle idéal et se
        retrouvent bloquées à l'acquisition d'un jeton. Le retard s'accumule
        en silence, les scans dérivent, et des annonces passent entre deux
        passages sans que rien ne le signale.

        On préfère un partage explicite : chaque source reçoit une part du
        budget pondérée par son poids, et le plancher qui en découle est
        appliqué à son intervalle. Le résultat est visible dans le dashboard.
        """
        poll = self.config.poll
        active = [
            s for s in self._sources.values()
            if s.config.enabled and not s.paused and s.config.interval is None
        ]
        if not active:
            return {"throttled_sources": 0, "floor": poll.min_interval}

        total_weight = sum(max(0.1, s.config.weight) for s in active)
        budget = max(0.1, self._bucket.rate)
        throttled = 0

        for state in active:
            share = budget * max(0.1, state.config.weight) / total_weight
            floor = max(poll.min_interval, 1.0 / share)
            if state.interval < floor:
                state.interval = min(poll.max_interval, floor)
                throttled += 1

        return {
            "throttled_sources": throttled,
            "floor": round(max(poll.min_interval, len(active) / budget), 2),
        }

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
            state.interval = min(self.config.poll.max_interval, state.interval * 1.5)

        self.bus.publish(
            "source_error", {"query": state.config.query, "error": state.last_error}
        )

    # ── Traitement des résultats ──────────────────────────────────────────
    async def _process(self, state: SourceState, listings: list[Listing]) -> int:
        if not listings:
            return 0

        self.total_items_seen += len(listings)
        state.items_seen += len(listings)

        fresh = [item for item in listings if item.id not in self._seen]
        for item in fresh:
            self._remember(item.id)
            # Le tampon reçoit TOUT, pas seulement ce qui matche : c'est ce qui
            # permet à un keyword ajouté plus tard de retrouver ces annonces.
            self.buffer.add(item)

        state.new_items += len(fresh)

        # Premier passage : on mémorise sans notifier, sinon les annonces déjà
        # en ligne partiraient toutes en notification.
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
            # première fois (cache purgé, source neuve) n'est pas une nouveauté.
            if max_age and listing.created and (now - listing.created) > max_age:
                self.drops["trop ancienne"] += 1
                continue
            if self._emit(listing, state=state):
                hits += 1

        if hits:
            await self.store.flush()
            self.bus.publish("stats", self.stats())

        return hits

    def _emit(
        self, listing: Listing, *, state: SourceState | None = None, backfill: bool = False
    ) -> bool:
        """Évalue une annonce et la diffuse si elle touche un keyword."""
        if listing.id in self._emitted:
            return False

        matched = self.matcher.match(listing.title, listing.price)
        if not matched:
            return False
        if self._is_excluded(listing.title):
            self.drops["bruit"] += 1
            return False

        listing.matched = matched
        listing.rarity, listing.rarity_color = rarity_of(listing.title, matched[0])

        # Lien de commande via le proxy d'achat, calculé une fois ici pour que
        # le dashboard, Discord et la base partagent exactement la même URL.
        if self.config.buyee.enabled:
            listing.buyee_url = buyee_url(
                listing.id,
                item_template=self.config.buyee.item_template,
                shop_template=self.config.buyee.shop_template,
                affiliate_id=self.config.buyee.affiliate_id,
            )

        self._remember_emitted(listing.id)
        self.total_hits += 1
        self._hit_times.append(time.time())
        for keyword in matched:
            self._keyword_hits[keyword] = self._keyword_hits.get(keyword, 0) + 1
        if state is not None:
            state.hits += 1
        if listing.latency_ms:
            self._latencies.append(listing.latency_ms)

        self.store.queue(listing)

        payload = listing.to_dict()
        payload["backfill"] = backfill
        self.feed.appendleft(payload)
        self.bus.publish("listing", payload)

        # Le backfill remonte du passé : on l'affiche, mais on ne réveille pas
        # Discord pour des annonces que l'utilisateur aurait pu déjà voir.
        if not backfill:
            for notifier in self.notifiers:
                notifier.notify(listing)
        return True

    def _is_excluded(self, title: str) -> bool:
        """Filet local : ce que l'exclusion côté serveur aurait laissé passer.

        `excludeKeyword` est tronqué pour rester d'une taille raisonnable, et
        rien ne garantit que Mercari l'applique aux formes composées. On
        revérifie donc ici, sur le titre normalisé.
        """
        if not self._exclude_terms:
            return False
        normalized = normalize(title)
        return any(contains_term(normalized, term) for term in self._exclude_terms)

    def refresh_filters(self) -> None:
        """Recompile les exclusions après une modification de la config."""
        terms = self.config.filters.all_exclude_terms()
        self._exclude_terms = tuple(
            normalized for term in terms if (normalized := normalize(term))
        )
        self._exclude_keyword = noise.exclude_keyword(terms)

    def _remember(self, item_id: str) -> None:
        """Ajoute au cache de dédup, en le gardant borné (FIFO)."""
        self._seen.add(item_id)
        self._seen_order.append(item_id)
        limit = self.config.storage.seen_cache_size
        while len(self._seen_order) > limit:
            self._seen.discard(self._seen_order.popleft())

    def _remember_emitted(self, item_id: str) -> None:
        self._emitted.add(item_id)
        self._emitted_order.append(item_id)
        while len(self._emitted_order) > 20_000:
            self._emitted.discard(self._emitted_order.popleft())

    # ── Requêtes trop larges ──────────────────────────────────────────────
    def _split_candidates(self) -> list[SourceState]:
        """Sources auto qui brûlent du budget sans rien rapporter.

        Le diagnostic est fait sur des mesures, pas sur la forme de la
        requête : une requête large peut très bien être rentable si le
        marché la remplit de pièces pertinentes. On attend donc d'avoir vu
        assez d'annonces pour que le rendement veuille dire quelque chose.
        """
        poll = self.config.poll
        if not poll.auto_split:
            return []

        out = []
        for state in self._sources.values():
            if not state.config.auto or not state.config.enabled:
                continue
            if state.items_seen < poll.split_min_items:
                continue
            if state.yield_ratio >= poll.split_min_yield:
                continue
            # Ne vaut que si des requêtes plus précises existent réellement.
            covered = [
                kw for kw in self.config.keywords
                if covers(state.config.query, kw)
                and normalize(kw) != normalize(state.config.query)
            ]
            if covered:
                out.append(state)
        return out

    async def _split_source(self, state: SourceState) -> list[str]:
        """Remplace une requête large par une requête précise par keyword."""
        query = state.config.query
        covered = [
            kw for kw in self.config.keywords
            if covers(query, kw) and normalize(kw) != normalize(query)
        ]
        created: list[str] = []
        for keyword in covered:
            if await self._add_source(keyword, page_size=60, auto=True):
                self._sources[keyword].split_from = query
                created.append(keyword)

        if not created:
            return []

        self._drop_source(query)
        log.warning(
            "requête '%s' remplacée : %d annonces examinées pour %d trouvaille(s) "
            "(rendement %.3f %%). Remplacée par %d requêtes précises : %s",
            query,
            state.items_seen,
            state.hits,
            state.yield_ratio * 100,
            len(created),
            ", ".join(created),
        )
        self.bus.publish(
            "source_split",
            {"query": query, "into": created, "items_seen": state.items_seen},
        )
        self._schedule_save()
        return created

    def _drop_source(self, query: str) -> None:
        state = self._sources.pop(query, None)
        if state is None:
            return
        if state.task is not None:
            state.task.cancel()
            if state.task in self._tasks:
                self._tasks.remove(state.task)
        self.config.sources = [
            source for source in self.config.sources if source.query != query
        ]

    # ── Entretien ─────────────────────────────────────────────────────────
    async def _maintenance_loop(self) -> None:
        """Flush périodique, purge, et remontée progressive du débit."""
        try:
            while self._running:
                await asyncio.sleep(30)
                await self.store.flush()
                self.buffer.prune()

                for state in self._split_candidates():
                    await self._split_source(state)
                self._allocate_budget()

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
    async def add_keyword(self, keyword: str) -> dict[str, Any]:
        """Ajoute un keyword, garantit sa couverture, et rattrape le passé."""
        keyword = " ".join(keyword.split())
        if not keyword:
            return {"added": False, "reason": "keyword vide"}
        if keyword in self.config.keywords:
            return {"added": False, "reason": "déjà présent"}

        async with self._lock:
            self.config.keywords.append(keyword)
            rule = self._make_rule(keyword)
            self.matcher.rules.append(rule)
            self.matcher.reindex()

            source_created = await self._ensure_coverage(keyword)
            backfilled = self._backfill(rule)

        self._schedule_save()
        log.info(
            "keyword ajouté: %s (source créée: %s, %d annonces rattrapées)",
            keyword,
            source_created or "aucune",
            backfilled,
        )
        self.bus.publish("stats", self.stats())
        return {
            "added": True,
            "keyword": keyword,
            "source_created": source_created,
            "backfilled": backfilled,
        }

    async def _ensure_coverage(self, keyword: str) -> str | None:
        """Crée une source si aucune requête existante ne ramène ce keyword."""
        for state in self._sources.values():
            if state.config.enabled and covers(state.config.query, keyword):
                return None

        # On interroge le keyword TEL QUEL. Élargir à son premier terme
        # (« ナイキ トレイル » → « ナイキ ») semblait économiser une requête ;
        # en réalité les 120 places de la page partaient à 99 % dans des
        # articles hors sujet, et la page débordait en permanence. Une
        # requête précise est filtrée par Mercari : ses résultats sont tous
        # pertinents et couvrent des heures au lieu de quelques secondes.
        if keyword in self._sources:
            return None

        await self._add_source(keyword, page_size=60, auto=True)
        return keyword

    async def _add_source(self, query: str, **kwargs: Any) -> bool:
        query = query.strip()
        if not query or query in self._sources:
            return False
        source = SourceConfig(query=query, **kwargs)
        state = SourceState(config=source, interval=self._interval_for(source))
        self._sources[query] = state
        self.config.sources.append(source)
        if self._running:
            self._spawn_source_task(state)
        log.info("source ajoutée: %s", query)
        return True

    async def add_source(self, query: str, **kwargs: Any) -> bool:
        added = await self._add_source(query, **kwargs)
        if added:
            self._schedule_save()
        return added

    def _backfill(self, rule: Rule) -> int:
        """Repasse le tampon récent avec la nouvelle règle.

        C'est ce qui donne un retour immédiat : ajouter un keyword remonte
        aussitôt ce qui correspond dans les dernières minutes scannées, au
        lieu d'attendre qu'une annonce toute neuve arrive.
        """
        max_age = self.config.filters.max_age_seconds
        now = time.time()
        count = 0

        for listing in self.buffer.snapshot(newest_first=True):
            if listing.id in self._emitted:
                continue
            if max_age and listing.created and (now - listing.created) > max_age:
                continue
            if not rule.matches(normalize(listing.title), listing.price):
                continue
            if self._emit(listing, backfill=True):
                count += 1
        return count

    async def remove_keyword(self, keyword: str) -> bool:
        if keyword not in self.config.keywords:
            return False

        async with self._lock:
            self.config.keywords.remove(keyword)
            self.matcher.rules = [
                rule for rule in self.matcher.rules if rule.keyword != keyword
            ]
            self.matcher.reindex()
            self._keyword_hits.pop(keyword, None)
            await self._prune_orphan_sources()

        self._schedule_save()
        log.info("keyword retiré: %s", keyword)
        self.bus.publish("stats", self.stats())
        return True

    async def _prune_orphan_sources(self) -> None:
        """Supprime les sources auto qui ne servent plus aucun keyword."""
        for query, state in list(self._sources.items()):
            if not state.config.auto:
                continue
            if any(covers(query, keyword) for keyword in self.config.keywords):
                continue
            self._drop_source(query)
            log.info("source retirée (plus aucun keyword): %s", query)

    def set_source_paused(self, query: str, paused: bool) -> bool:
        state = self._sources.get(query)
        if state is None:
            return False
        state.paused = paused
        return True

    # ── Persistance de la configuration ───────────────────────────────────
    def _schedule_save(self) -> None:
        """Sauvegarde différée : plusieurs ajouts d'affilée = une écriture."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # hors boucle (tests unitaires) : rien à planifier
        if self._save_task is not None and not self._save_task.done():
            self._save_task.cancel()
        self._save_task = loop.create_task(self._save_after_delay())

    async def _save_after_delay(self, delay: float = 1.5) -> None:
        try:
            await asyncio.sleep(delay)
            await asyncio.to_thread(self.config.save)
            log.debug("configuration sauvegardée")
            self.bus.publish("config_saved", {"keywords": len(self.config.keywords)})
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("échec de la sauvegarde de la configuration")

    # ── Métriques ─────────────────────────────────────────────────────────
    def activity(self, minutes: int = 30) -> list[int]:
        """Trouvailles par minute, de la plus ancienne à la plus récente."""
        now = time.time()
        buckets = [0] * minutes
        for timestamp in self._hit_times:
            age_minutes = int((now - timestamp) // 60)
            if 0 <= age_minutes < minutes:
                buckets[minutes - 1 - age_minutes] += 1
        return buckets

    def keyword_stats(self) -> list[dict[str, Any]]:
        return sorted(
            (
                {
                    "keyword": keyword,
                    "hits": self._keyword_hits.get(keyword, 0),
                    "covered": any(
                        state.config.enabled and covers(state.config.query, keyword)
                        for state in self._sources.values()
                    ),
                    # Intervalle réel de la source la plus rapide qui couvre
                    # ce keyword : « à quelle vitesse suis-je surveillé ? »
                    "interval": round(
                        min(
                            (
                                state.interval
                                for state in self._sources.values()
                                if state.config.enabled
                                and not state.paused
                                and covers(state.config.query, keyword)
                            ),
                            default=0.0,
                        ),
                        1,
                    ),
                }
                for keyword in self.config.keywords
            ),
            key=lambda entry: (-entry["hits"], entry["keyword"]),
        )

    def coverage(self) -> dict[str, Any]:
        """Le budget de requêtes suffit-il à tenir la cadence visée ?"""
        active = [s for s in self._sources.values() if s.config.enabled and not s.paused]
        demand = sum(1.0 / max(0.1, state.interval) for state in active)
        uncovered = [
            entry["keyword"] for entry in self.keyword_stats() if not entry["covered"]
        ]
        budget = self._bucket.rate
        # Intervalle réellement tenable si toutes les sources se partagent
        # le budget à parts égales — le chiffre à montrer à l'utilisateur
        # quand il se demande à quelle vitesse son keyword est réellement
        # surveillé.
        effective = len(active) / budget if active and budget else 0.0
        # Saturation = la cadence VOULUE dépasse le budget. La mesurer après
        # répartition ne dirait jamais rien : `_allocate_budget()` ramène
        # justement la demande au niveau du budget. Ce qu'il faut signaler,
        # c'est que la cadence demandée n'est pas tenue.
        wanted = sum(
            1.0 / max(0.1, state.config.interval or self.config.poll.interval)
            for state in active
        )
        return {
            "sources": len(active),
            "demand_per_second": round(demand, 2),
            "wanted_per_second": round(wanted, 2),
            "budget_per_second": round(budget, 2),
            "saturated": wanted > budget * 1.02,
            "effective_interval": round(max(self.config.poll.min_interval, effective), 1),
            "uncovered_keywords": uncovered,
            "low_yield": [
                state.config.query for state in self._split_candidates()
            ],
        }

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
            "total_overflows": self.total_overflows,
            "seen_cache": len(self._seen),
            "buffer_size": len(self.buffer),
            "polls_per_minute": round(self.total_polls / uptime * 60, 1) if uptime else 0,
            "rate_limit": round(self._bucket.rate, 2),
            "throttled": time.monotonic() < self._throttle_until,
            "latency_p50_ms": percentile(0.50),
            "latency_p95_ms": percentile(0.95),
            "yield_ratio": round(
                self.total_hits / self.total_items_seen, 5
            ) if self.total_items_seen else 0.0,
            "drops": dict(self.drops),
            "filtered_terms": len(self._exclude_terms),
            "notifiers": {n.name: n.stats() for n in self.notifiers},
        }

    def sources_state(self) -> list[dict[str, Any]]:
        return sorted(
            (state.to_dict() for state in self._sources.values()),
            key=lambda entry: (-entry["hits"], entry["query"]),
        )
