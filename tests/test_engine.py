"""Tests du moteur : warmup, déduplication, filtres, backoff."""

import asyncio
import time

import pytest

from mercari_sniper.backends.base import BackendError, SearchPage, SearchQuery
from mercari_sniper.config import Config, SourceConfig
from mercari_sniper.engine import SniperEngine
from mercari_sniper.events import EventBus
from mercari_sniper.models import Listing
from mercari_sniper.store import Store


class FakeBackend:
    """Backend piloté par le test : renvoie ce qu'on lui donne."""

    name = "fake"

    def __init__(self, batches=None):
        self.batches = list(batches or [])
        self.calls = 0
        self.raise_next = None

    async def search(self, query: SearchQuery):
        self.calls += 1
        if self.raise_next is not None:
            error, self.raise_next = self.raise_next, None
            raise error
        items = self.batches.pop(0) if self.batches else []
        if isinstance(items, SearchPage):
            return items
        return SearchPage(items=items)

    async def aclose(self):
        return None


def make_listing(item_id, title, price=5000, created=None):
    now = time.time()
    return Listing(
        id=item_id,
        title=title,
        price=price,
        url=f"https://jp.mercari.com/item/{item_id}",
        created=int(created if created is not None else now),
        detected_at=now,
    )


class CaptureNotifier:
    name = "capture"

    def __init__(self):
        self.items = []

    async def start(self):
        pass

    async def stop(self):
        pass

    def notify(self, listing):
        self.items.append(listing)

    def stats(self):
        return {"count": len(self.items)}


@pytest.fixture
async def context(tmp_path):
    """Fournit (config, store, bus) prêts à l'emploi."""
    config = Config()
    config.keywords = ["nike trail", "nike acg", "under armour storm"]
    config.sources = [SourceConfig(query="nike")]
    config.storage.database = str(tmp_path / "test.db")
    config.poll.warmup = True
    config.notify.console = False

    store = Store(config.storage.database)
    await store.open()
    yield config, store, EventBus()
    await store.close()


class TestWarmup:
    async def test_first_poll_notifies_nothing(self, context):
        config, store, bus = context
        backend = FakeBackend([[make_listing("m1", "Nike Trail Jacket")]])
        notifier = CaptureNotifier()
        engine = SniperEngine(config, backend, store, bus, [notifier])

        state = engine._sources["nike"]
        await engine._poll_source(state)

        assert notifier.items == []
        assert state.warmed_up
        assert engine.total_hits == 0

    async def test_second_poll_notifies_new_items(self, context):
        config, store, bus = context
        backend = FakeBackend([
            [make_listing("m1", "Nike Trail Jacket")],
            [make_listing("m1", "Nike Trail Jacket"),
             make_listing("m2", "Nike ACG Vest")],
        ])
        notifier = CaptureNotifier()
        engine = SniperEngine(config, backend, store, bus, [notifier])
        state = engine._sources["nike"]

        await engine._poll_source(state)   # warmup
        await engine._poll_source(state)   # m2 est nouveau

        assert [item.id for item in notifier.items] == ["m2"]
        assert engine.total_hits == 1

    async def test_warmup_disabled_notifies_immediately(self, context):
        config, store, bus = context
        config.poll.warmup = False
        backend = FakeBackend([[make_listing("m1", "Nike Trail Jacket")]])
        notifier = CaptureNotifier()
        engine = SniperEngine(config, backend, store, bus, [notifier])

        await engine._poll_source(engine._sources["nike"])
        assert len(notifier.items) == 1


class TestDeduplication:
    async def test_same_item_notified_once(self, context):
        config, store, bus = context
        config.poll.warmup = False
        item = make_listing("m1", "Nike Trail Jacket")
        backend = FakeBackend([[item], [item], [item]])
        notifier = CaptureNotifier()
        engine = SniperEngine(config, backend, store, bus, [notifier])
        state = engine._sources["nike"]

        for _ in range(3):
            await engine._poll_source(state)

        assert len(notifier.items) == 1

    async def test_seen_cache_is_bounded(self, context):
        config, store, bus = context
        config.storage.seen_cache_size = 10
        engine = SniperEngine(config, FakeBackend(), store, bus)

        for i in range(50):
            engine._remember(f"m{i}")

        assert len(engine._seen) == 10
        assert "m49" in engine._seen      # le plus récent est gardé
        assert "m0" not in engine._seen   # le plus ancien est évincé

    async def test_seen_cache_primed_from_store(self, context):
        config, store, bus = context
        config.poll.warmup = False
        await store.mark_seen_bulk(["m1"])

        backend = FakeBackend([[make_listing("m1", "Nike Trail Jacket")]])
        notifier = CaptureNotifier()
        engine = SniperEngine(config, backend, store, bus, [notifier])
        await engine.start()
        try:
            await asyncio.sleep(0.05)
            # m1 était déjà connu de la base : aucune notification.
            assert notifier.items == []
        finally:
            await engine.stop()


class TestFiltering:
    async def test_unmatched_titles_are_ignored(self, context):
        config, store, bus = context
        config.poll.warmup = False
        backend = FakeBackend([[make_listing("m1", "Adidas Ultraboost")]])
        notifier = CaptureNotifier()
        engine = SniperEngine(config, backend, store, bus, [notifier])

        await engine._poll_source(engine._sources["nike"])
        assert notifier.items == []

    async def test_stale_listings_are_ignored(self, context):
        config, store, bus = context
        config.poll.warmup = False
        config.filters.max_age_seconds = 300
        old = make_listing("m1", "Nike Trail Jacket", created=time.time() - 4000)
        backend = FakeBackend([[old]])
        notifier = CaptureNotifier()
        engine = SniperEngine(config, backend, store, bus, [notifier])

        await engine._poll_source(engine._sources["nike"])
        assert notifier.items == []

    async def test_exclude_words_veto(self, context):
        config, store, bus = context
        config.poll.warmup = False
        config.filters.exclude_words = ["ジャンク"]
        backend = FakeBackend([[make_listing("m1", "Nike Trail Jacket ジャンク")]])
        notifier = CaptureNotifier()
        engine = SniperEngine(config, backend, store, bus, [notifier])

        await engine._poll_source(engine._sources["nike"])
        assert notifier.items == []

    async def test_rarity_is_assigned(self, context):
        config, store, bus = context
        config.poll.warmup = False
        backend = FakeBackend([[make_listing("m1", "Nike Trail Jacket")]])
        notifier = CaptureNotifier()
        engine = SniperEngine(config, backend, store, bus, [notifier])

        await engine._poll_source(engine._sources["nike"])
        assert notifier.items[0].rarity == "RARE"
        assert notifier.items[0].matched == ["nike trail"]


class TestErrorHandling:
    async def test_rate_limit_lowers_global_budget(self, context):
        config, store, bus = context
        backend = FakeBackend()
        backend.raise_next = BackendError("429", status=429, retry_after=1.0)
        engine = SniperEngine(config, backend, store, bus)

        before = engine._bucket.rate
        await engine._poll_source(engine._sources["nike"])

        assert engine._bucket.rate < before
        assert engine._sources["nike"].errors == 1

    async def test_repeated_errors_widen_the_interval(self, context):
        config, store, bus = context
        backend = FakeBackend()
        engine = SniperEngine(config, backend, store, bus)
        state = engine._sources["nike"]
        original = state.interval

        for _ in range(3):
            backend.raise_next = BackendError("boom")
            await engine._poll_source(state)

        assert state.interval > original

    async def test_success_resets_the_error_streak(self, context):
        config, store, bus = context
        backend = FakeBackend([[]])
        engine = SniperEngine(config, backend, store, bus)
        state = engine._sources["nike"]

        backend.raise_next = BackendError("boom")
        await engine._poll_source(state)
        assert state.consecutive_errors == 1

        await engine._poll_source(state)
        assert state.consecutive_errors == 0


class TestHotReload:
    async def test_add_and_remove_keyword(self, context):
        config, store, bus = context
        engine = SniperEngine(config, FakeBackend(), store, bus)

        assert (await engine.add_keyword("nike phenom"))["added"]
        assert not (await engine.add_keyword("nike phenom"))["added"]   # doublon
        assert engine.matcher.match("Nike Phenom Elite Pants", 5000) == ["nike phenom"]

        assert await engine.remove_keyword("nike phenom")
        assert engine.matcher.match("Nike Phenom Elite Pants", 5000) == []

    async def test_pause_source(self, context):
        config, store, bus = context
        engine = SniperEngine(config, FakeBackend(), store, bus)
        assert engine.set_source_paused("nike", True)
        assert engine._sources["nike"].paused
        assert not engine.set_source_paused("inconnue", True)


class TestStats:
    async def test_stats_expose_key_metrics(self, context):
        config, store, bus = context
        config.poll.warmup = False
        backend = FakeBackend([[make_listing("m1", "Nike Trail Jacket")]])
        engine = SniperEngine(config, backend, store, bus)

        await engine._poll_source(engine._sources["nike"])
        stats = engine.stats()

        assert stats["total_hits"] == 1
        assert stats["total_polls"] == 1
        assert stats["keywords"] == 3
        assert "latency_p50_ms" in stats
