"""Scoring, devises, circuit breaker, bus, base — et le pipeline complet."""

import asyncio
import time

import pytest

from radar.adapters.base import AdapterError, SearchResult
from radar.core.circuit_breaker import CircuitBreaker, State
from radar.core.currency import CurrencyConverter
from radar.core.event_bus import EventBus
from radar.core.keywords import Keyword
from radar.core.matcher import FilterEngine, GlobalFilters
from radar.core.scanner import Scanner
from radar.core.scheduler import Scheduler
from radar.core.scoring import ScoringConfig, ScoringEngine, tier_of
from radar.notifications.dispatcher import NotificationHub
from radar.storage.database import Database

from .helpers import CaptureNotifier, ScriptedAdapter, make_listing

FAST = {"high": 0.05, "medium": 0.05, "low": 0.05}
SOLO = [Keyword.build("Nike", search=["ナイキ"], include=["ナイキ", "nike"])]


@pytest.fixture
async def database(tmp_path):
    db = Database(tmp_path / "radar.db")
    await db.open()
    yield db
    await db.close()


def build(adapters, keywords, database, hub, **kwargs):
    return Scanner(
        adapters=adapters,
        keywords=keywords,
        filters=FilterEngine(
            keywords=keywords, globals_=GlobalFilters.build(max_age_seconds=0)
        ),
        hub=hub,
        database=database,
        bus=kwargs.pop("bus", EventBus()),
        scheduler=Scheduler(
            intervals=dict(kwargs.pop("intervals", FAST)),
            budget_per_second=kwargs.pop("budget", 200.0),
            jitter=0.0,
        ),
        flush_every=kwargs.pop("flush_every", 1.0),
        **kwargs,
    )


# ── Scoring ───────────────────────────────────────────────────────────────
class TestScoring:
    def test_tiers(self):
        assert tier_of(95) == "ULTRA RARE"
        assert tier_of(75) == "VERY RARE"
        assert tier_of(55) == "RARE"
        assert tier_of(10) == "NORMAL"

    def test_no_keyword_scores_zero(self):
        engine = ScoringEngine()
        assert engine.score(make_listing(title="chaise")).total == 0

    def test_exact_title_match_scores_higher(self):
        engine = ScoringEngine()
        base = make_listing(title="quelque chose", keywords=["Nike Division"])
        exact = make_listing(title="nike division jacket", keywords=["Nike Division"])
        assert engine.score(exact).total > engine.score(base).total

    def test_rare_line_adds_points(self):
        engine = ScoringEngine()
        plain = make_listing(title="ナイキ tシャツ", keywords=["k"])
        rare = make_listing(title="ナイキ ギャクソウ tシャツ", keywords=["k"])
        assert engine.score(rare).total > engine.score(plain).total

    def test_low_price_is_a_bonus(self):
        engine = ScoringEngine(ScoringConfig(bargain_price=5000))
        cheap = make_listing(price=3000, keywords=["k"])
        normal = make_listing(price=20000, keywords=["k"])
        assert engine.score(cheap).total > engine.score(normal).total

    def test_score_is_bounded(self):
        engine = ScoringEngine()
        item = make_listing(
            title="ナイキ ギャクソウ acg tokyo veilance division", price=1000,
            keywords=["Nike"], source="jdi_fleamarket",
        )
        assert 0 <= engine.score(item).total <= 100

    def test_breakdown_is_explainable(self):
        """Un score de 94 doit pouvoir se justifier, sinon on ne peut pas
        régler les seuils de notification."""
        engine = ScoringEngine()
        breakdown = engine.score(make_listing(keywords=["Nike Division"]))
        assert breakdown.parts
        assert breakdown.explain()

    def test_statistical_signals_stay_silent_without_data(self):
        """Une rareté calculée sur trois annonces ne veut rien dire."""
        engine = ScoringEngine()
        breakdown = engine.score(make_listing(keywords=["k"], seller="s1"))
        assert breakdown.parts["rareté observée"] == 0
        assert breakdown.parts["vendeur"] == 0

    def test_signals_activate_after_enough_samples(self):
        engine = ScoringEngine(ScoringConfig(demand_min_samples=5))
        for index in range(10):
            engine.observe(make_listing(f"m{index}", keywords=["fréquent"], seller="spam"))
        rare = engine.score(make_listing(keywords=["rare"], seller="inconnu"))
        assert rare.parts["rareté observée"] > 0

    def test_flooding_seller_is_penalised(self):
        engine = ScoringEngine(ScoringConfig(demand_min_samples=5))
        for index in range(12):
            engine.observe(make_listing(f"m{index}", keywords=["k"], seller="revendeur"))
        breakdown = engine.score(make_listing(keywords=["k"], seller="revendeur"))
        assert breakdown.parts["vendeur"] < 0


# ── Devises ───────────────────────────────────────────────────────────────
class TestCurrency:
    def test_conversion_is_local(self):
        converter = CurrencyConverter(fixed_rate=0.006)
        assert converter.convert(10000) == pytest.approx(60.0)

    def test_format_marks_it_as_an_approximation(self):
        assert CurrencyConverter(fixed_rate=0.006).format(10000).startswith("≈")

    def test_zero_amount(self):
        assert CurrencyConverter(fixed_rate=0.006).convert(0) == 0.0

    def test_fixed_rate_is_never_stale(self):
        assert CurrencyConverter(fixed_rate=0.006).stale is False

    def test_fallback_rate_is_marked_stale(self):
        """Un taux de repli est forcément périmé — le dire évite de faire
        passer une approximation pour une conversion."""
        assert CurrencyConverter(target="EUR").stale is True

    async def test_refresh_is_skipped_without_network_permission(self):
        assert await CurrencyConverter(target="EUR", allow_network=False).refresh() is False

    def test_status_exposes_provenance(self):
        status = CurrencyConverter(fixed_rate=0.006).status()
        assert status["source"] == "config"
        assert status["available"] is True


# ── Circuit breaker ───────────────────────────────────────────────────────
class TestCircuitBreaker:
    def test_opens_after_threshold(self):
        breaker = CircuitBreaker(name="s", threshold=3)
        for _ in range(3):
            breaker.record_failure("boom", now=0)
        assert breaker.state is State.OPEN
        assert breaker.allows(now=0) is False

    def test_open_circuit_returns_immediately(self):
        """Une source coupée ne doit rien coûter, pas même un timeout."""
        breaker = CircuitBreaker(name="s", threshold=1, cooldown=30)
        breaker.record_failure("boom", now=0)
        assert breaker.allows(now=5) is False

    def test_half_open_after_cooldown(self):
        breaker = CircuitBreaker(name="s", threshold=1, cooldown=10)
        breaker.record_failure("boom", now=0)
        assert breaker.allows(now=11) is True
        assert breaker.state is State.HALF_OPEN

    def test_success_closes_the_circuit(self):
        breaker = CircuitBreaker(name="s", threshold=1, cooldown=1)
        breaker.record_failure("boom", now=0)
        breaker.allows(now=2)
        breaker.record_success()
        assert breaker.state is State.CLOSED

    def test_cooldown_grows_on_repeated_failure(self):
        """Une source durablement morte ne doit pas être retestée sans fin."""
        breaker = CircuitBreaker(name="s", threshold=1, cooldown=10)
        breaker.record_failure("boom", now=0)
        breaker.allows(now=11)
        breaker.record_failure("encore", now=11)
        assert breaker.current_cooldown > 10

    def test_cooldown_is_capped(self):
        breaker = CircuitBreaker(name="s", threshold=1, cooldown=10, max_cooldown=40)
        now = 0.0
        for _ in range(10):
            breaker.record_failure("boom", now=now)
            now += breaker.current_cooldown + 1
            breaker.allows(now=now)
        assert breaker.current_cooldown <= 40

    def test_history_is_bounded(self):
        breaker = CircuitBreaker(name="s", threshold=1, cooldown=0.001)
        for index in range(60):
            breaker.record_failure("x", now=index)
            breaker.allows(now=index + 1)
            breaker.record_success()
        assert len(breaker.history) <= 20


# ── Bus d'événements ──────────────────────────────────────────────────────
class TestEventBus:
    async def test_subscriber_receives_events(self):
        bus = EventBus()
        received = []

        async def listen():
            async for event in bus.subscribe(replay=False):
                received.append(event)
                break

        task = asyncio.create_task(listen())
        await asyncio.sleep(0.05)
        bus.publish("listing", {"x": 1})
        await asyncio.wait_for(task, timeout=2)
        assert received[0].type == "listing"

    async def test_replay_gives_context_to_a_new_client(self):
        bus = EventBus(replay=5)
        for index in range(3):
            bus.publish("listing", {"i": index})
        received = []

        async def listen():
            async for event in bus.subscribe():
                received.append(event)
                if len(received) == 3:
                    break

        await asyncio.wait_for(asyncio.create_task(listen()), timeout=2)
        assert len(received) == 3

    def test_publish_is_synchronous_and_fast(self):
        """Appelé depuis le chemin critique : il ne doit jamais bloquer."""
        bus = EventBus()
        started = time.perf_counter()
        for index in range(5000):
            bus.publish("listing", {"i": index})
        assert (time.perf_counter() - started) < 0.5

    async def test_a_slow_subscriber_does_not_block_the_publisher(self):
        bus = EventBus(queue_size=4)
        async def listen():
            async for _ in bus.subscribe(replay=False):
                await asyncio.sleep(10)      # abonné volontairement bloqué
        task = asyncio.create_task(listen())
        await asyncio.sleep(0.05)
        for index in range(100):
            bus.publish("listing", {"i": index})
        assert bus.dropped > 0               # c'est LUI qui perd, pas l'émetteur
        task.cancel()


# ── Pipeline complet ──────────────────────────────────────────────────────
class TestPipeline:
    async def test_warmup_notifies_nothing(self, database, keywords):
        adapter = ScriptedAdapter(pages=[[make_listing(source="scripted")]])
        notifier = CaptureNotifier()
        hub = NotificationHub([notifier])
        await hub.start()

        scanner = build({"scripted": adapter}, keywords, database, hub)
        await scanner.start()
        await asyncio.sleep(0.4)
        await scanner.stop()
        await hub.stop()
        assert notifier.received == []

    async def test_second_pass_scores_and_notifies(self, database):
        adapter = ScriptedAdapter(pages=[
            [make_listing("m1", title="ナイキ ジャケット", source="scripted")],
            [make_listing("m1", title="ナイキ ジャケット", source="scripted"),
             make_listing("m2", title="ナイキ ギャクソウ ベスト", source="scripted")],
        ])
        notifier = CaptureNotifier()
        hub = NotificationHub([notifier])
        await hub.start()

        scanner = build({"scripted": adapter}, SOLO, database, hub)
        await scanner.start()
        await asyncio.sleep(1.0)
        await scanner.stop()
        await hub.drain()
        await hub.stop()

        assert [item.listing_id for item in notifier.received] == ["m2"]
        assert notifier.received[0].score > 0
        assert notifier.received[0].tier

    async def test_scanner_never_waits_for_notifications(self, database, keywords):
        """Critère absolu (§44). Vérifié en comptant les requêtes."""
        slow = CaptureNotifier(delay=0.5)
        hub = NotificationHub([slow])
        await hub.start()

        adapter = ScriptedAdapter(
            pages=[[make_listing(f"m{i}", source="scripted")] for i in range(10)]
        )
        scanner = build({"scripted": adapter}, keywords, database, hub)
        await scanner.start()
        await asyncio.sleep(1.5)
        await scanner.stop()
        await hub.stop()

        assert len(adapter.queries) > 4, (
            f"{len(adapter.queries)} requêtes seulement : le scanner a attendu"
        )

    async def test_failing_adapter_does_not_stop_the_others(self, database, keywords):
        broken = ScriptedAdapter(source="broken", fail=True)
        healthy = ScriptedAdapter(source="ok", pages=[
            [make_listing("m1", source="ok")],
            [make_listing("m2", title="ナイキ トレイル", source="ok")],
        ])
        hub = NotificationHub([])
        scanner = build({"broken": broken, "ok": healthy}, keywords, database, hub)
        await scanner.start()
        await asyncio.sleep(1.0)
        await scanner.stop()

        assert len(healthy.queries) >= 2
        assert scanner.metrics.source("broken").errors.total >= 1

    async def test_circuit_opens_and_spares_the_budget(self, database, keywords):
        broken = ScriptedAdapter(source="broken", fail=True)
        hub = NotificationHub([])
        scanner = build({"broken": broken}, keywords, database, hub)
        await scanner.start()
        await asyncio.sleep(1.2)
        await scanner.stop()

        breaker = scanner.breakers["broken"]
        assert breaker.state is not State.CLOSED
        assert breaker.trips >= 1

    async def test_rate_limit_lowers_the_global_budget(self, database, keywords):
        """Un 429 est par adresse IP : ralentir une source ne suffirait pas."""
        adapter = ScriptedAdapter(
            error=AdapterError("429", status=429, retry_after=0.1)
        )
        hub = NotificationHub([])
        scanner = build({"s": adapter}, keywords, database, hub, budget=10.0)
        await scanner.start()
        await asyncio.sleep(0.4)
        assert scanner.scheduler.budget_per_second < 10.0
        await scanner.stop()

    async def test_gap_triggers_pagination(self, database):
        now = time.time()
        adapter = ScriptedAdapter(pages=[
            SearchResult(source="scripted", requested_at=now, received_at=now,
                         listings=[make_listing("old", title="ナイキ 旧",
                                                created_at=now - 100, source="scripted")]),
            SearchResult(source="scripted", cursor="page2",
                         requested_at=now, received_at=now,
                         listings=[make_listing(f"n{i}", title="ナイキ 新",
                                                created_at=now - 1 + i, source="scripted")
                                   for i in range(3)]),
            SearchResult(source="scripted", requested_at=now, received_at=now,
                         listings=[make_listing("old", title="ナイキ 旧",
                                                created_at=now - 100, source="scripted")]),
        ])
        hub = NotificationHub([])
        scanner = build({"scripted": adapter}, SOLO, database, hub)
        await scanner.start()
        await asyncio.sleep(1.0)
        await scanner.stop()

        assert any(q.cursor == "page2" for q in adapter.queries), "trou non rattrapé"

    async def test_events_reach_the_bus(self, database):
        bus = EventBus()
        adapter = ScriptedAdapter(pages=[
            [make_listing("m1", title="ナイキ a", source="scripted")],
            [make_listing("m2", title="ナイキ b", source="scripted")],
        ])
        hub = NotificationHub([])
        scanner = build({"scripted": adapter}, SOLO, database, hub, bus=bus)
        await scanner.start()
        await asyncio.sleep(0.8)
        await scanner.stop()

        assert any(e.type == "listing" for e in bus._recent)

    async def test_listings_are_persisted_quickly(self, database):
        """Le dashboard lit la base : une purge toutes les 30 s le laisserait
        vide une demi-minute après chaque ouverture."""
        adapter = ScriptedAdapter(pages=[
            [make_listing("m1", title="ナイキ a", source="scripted")],
            [make_listing("m2", title="ナイキ b", source="scripted")],
        ])
        hub = NotificationHub([])
        scanner = build({"scripted": adapter}, SOLO, database, hub, flush_every=1.0)
        await scanner.start()
        await asyncio.sleep(2.0)
        await scanner.stop()

        assert len(await database.recent(10)) >= 1

    async def test_a_failing_source_is_reported_not_hidden(self, database, keywords):
        """Une source en échec reste visible dans les compteurs."""
        adapter = ScriptedAdapter("mercari", error=AdapterError("HTTP 503"))
        hub = NotificationHub([CaptureNotifier()])
        await hub.start()
        scanner = build({"mercari": adapter}, keywords, database, hub)
        await scanner.start()
        await asyncio.sleep(0.35)
        await scanner.stop()

        stats = scanner.metrics.peek("mercari")
        assert stats is not None and stats.errors.total >= 1

class TestDatabase:
    async def test_round_trip(self, database):
        item = make_listing("m1", source="mercari")
        item.keywords = ["Nike Division"]
        item.keyword = "Nike Division"
        item.score = 82
        item.tier = "VERY RARE"
        database.queue(item)
        assert await database.flush() == 1

        rows = await database.recent(10)
        assert rows[0]["listing_id"] == "m1"
        assert rows[0]["score"] == 82
        assert rows[0]["keywords"] == ["Nike Division"]

    async def test_duplicate_insert_is_ignored(self, database):
        item = make_listing("m1", source="mercari")
        database.queue(item)
        database.queue(item)
        await database.flush()
        assert len(await database.recent(10)) == 1

    async def test_search_filters(self, database):
        for index in range(6):
            item = make_listing(f"m{index}", source="mercari", title=f"ナイキ {index}")
            item.score = index * 20
            item.keyword = "Nike"
            database.queue(item)
        await database.flush()

        assert len(await database.search_listings(min_score=60)) == 3
        assert len(await database.search_listings(source="rakuma")) == 0
        assert len(await database.search_listings(text="ナイキ")) == 6
        assert len(await database.search_listings(keyword="Nike")) == 6

    async def test_analytics_shape(self, database):
        item = make_listing("m1", source="mercari")
        item.keyword = "Nike"
        item.tier = "RARE"
        database.queue(item)
        await database.flush()

        analytics = await database.analytics(60)
        assert analytics["by_source"][0]["source"] == "mercari"
        assert analytics["by_tier"][0]["tier"] == "RARE"
        assert analytics["timeline"]

    async def test_seen_survives_restart(self, tmp_path):
        db = Database(tmp_path / "restart.db")
        await db.open()
        await db.mark_seen(["mercari:m1"])
        await db.close()

        again = Database(tmp_path / "restart.db")
        await again.open()
        assert "mercari:m1" in await again.load_seen()
        await again.close()


class TestConfigIsolation:
    """Les générations successives cohabitent dans le même dépôt.

    Si deux d'entre elles visaient le même fichier, l'une démarrerait en
    silence avec ses valeurs par défaut — un mot-clé effacé sans un mot.
    """

    def test_radar_does_not_share_snipe_config_file(self):
        from radar.config.loader import DEFAULT_PATH as radar_path

        snipe = pytest.importorskip("snipe.config.settings")
        assert radar_path != snipe.DEFAULT_PATH

    def test_secrets_never_reach_the_yaml(self, tmp_path):
        from radar.config.loader import Settings

        settings = Settings()
        settings.telegram_token = "123:SECRET"
        settings.telegram_chat_id = "42"
        settings.discord_webhook = "https://discord.com/api/webhooks/1/x"

        written = settings.save(tmp_path / "radar.yaml").read_text("utf-8")
        for secret in ("123:SECRET", "discord.com/api/webhooks"):
            assert secret not in written


class TestKeywordMatchingFallback:
    """Le bug qui rendait le bot muet.

    Un mot-clé sans `include` se rabattait sur son NOM lisible pour filtrer
    les titres. Nommé « Nike » avec la recherche « ナイキ », il exigeait le
    mot « Nike » dans un titre japonais — donc jamais aucun résultat, et
    rien pour le signaler : ni erreur, ni compteur, juste un flux vide.
    """

    def _match(self, keyword, title="ナイキ ACG ジャケット 新品未使用"):
        from radar.adapters.base import Listing
        from radar.core.matcher import FilterEngine, GlobalFilters

        now = time.time()
        engine = FilterEngine(
            keywords=[keyword],
            globals_=GlobalFilters.build(max_age_seconds=900),
        )
        listing = Listing(
            source="mercari", listing_id="m1", title=title, url="u",
            price=12000, created_at=now - 10, detected_at=now,
        )
        return engine.match(listing, now=now)

    def test_a_latin_name_with_a_japanese_search_still_matches(self):
        assert self._match(Keyword.build("Nike", search=["ナイキ"])) == ["Nike"]

    def test_the_name_can_be_anything(self):
        """Le nom est une étiquette pour l'utilisateur, pas un filtre."""
        assert self._match(
            Keyword.build("Veste rouge", search=["ナイキ"])
        ) == ["Veste rouge"]

    def test_multiword_search_requires_every_word(self):
        assert self._match(Keyword.build("K", search=["ナイキ ACG"])) == ["K"]
        assert self._match(Keyword.build("K", search=["ナイキ アークテリクス"])) == []

    def test_several_searches_are_alternatives(self):
        keyword = Keyword.build("K", search=["アークテリクス", "ナイキ"])
        assert self._match(keyword) == ["K"]

    def test_include_still_wins_over_search(self):
        assert self._match(Keyword.build("K", search=["ナイキ"], include=["ACG"])) == ["K"]
        assert self._match(Keyword.build("K", search=["ナイキ"], include=["gore"])) == []

    def test_a_name_alone_still_works(self):
        assert self._match(Keyword.build("ナイキ")) == ["ナイキ"]

    def test_discrimination_is_preserved(self):
        """Le correctif ne doit pas tout laisser passer."""
        assert self._match(Keyword.build("A", search=["アークテリクス"])) == []
        assert self._match(Keyword.build("Nike", search=["ナイキ"], exclude=["新品"])) == []

    def test_every_alternative_is_indexed(self):
        """Second bug : seule la PREMIÈRE alternative réveillait le mot-clé.

        Un mot-clé « アークテリクス OU ナイキ » n'était testé que sur les
        titres contenant アークテリクス. Tous les ナイキ passaient à côté,
        sans erreur ni compteur.
        """
        keyword = Keyword.build("K", search=["アークテリクス", "ナイキ"])
        assert keyword.pivots == ("アークテリクス", "ナイキ")
        assert self._match(keyword, "ナイキ ACG ジャケット") == ["K"]
        assert self._match(keyword, "アークテリクス ベータ") == ["K"]
        assert self._match(keyword, "アディダス サンバ") == []

    def test_alternatives_from_include_are_indexed_too(self):
        keyword = Keyword.build(
            "K", search=["ナイキ"], include=["ディビジョン", "division"]
        )
        assert self._match(keyword, "ナイキ ディビジョン パンツ") == ["K"]
        assert self._match(keyword, "NIKE division pants") == ["K"]
        assert self._match(keyword, "ナイキ トレイル パンツ") == []


class TestApiValidatesWhatItWrites:
    """L'API écrit dans radar.yaml : elle doit valider comme le loader.

    Sans ça, un `min_price: "cher"` posté par l'interface partait droit dans
    le chemin critique, où comparer un prix à une chaîne lève une exception
    à chaque annonce examinée.
    """

    def _upsert(self, payload):
        from radar.api.server import AppContext
        from radar.config.loader import Settings

        context = AppContext(Settings(), database=None, bus=None)
        context.upsert_keyword(payload)
        return context.settings.keywords[0]

    def test_a_price_that_is_not_a_number_is_refused(self):
        assert self._upsert({"name": "k", "min_price": "cher"}).min_price is None

    def test_a_string_search_becomes_a_list(self):
        """Sinon `Keyword.build` itère les CARACTÈRES de la chaîne."""
        assert self._upsert({"name": "k", "search": "nike"}).search == ["nike"]

    def test_an_unknown_priority_falls_back(self):
        spec = self._upsert({"name": "k", "priority": "urgent"})
        from radar.core.keywords import Keyword

        assert Keyword.build(spec.name, priority=spec.priority).priority == "medium"

    def test_unknown_fields_are_dropped(self):
        spec = self._upsert({"name": "k", "sudo": True})
        assert not hasattr(spec, "sudo")

    def test_the_name_always_wins(self):
        assert self._upsert({"name": "Nike ACG"}).name == "Nike ACG"
