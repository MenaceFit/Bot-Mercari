"""Non-régression des trois bugs signalés.

1. Des annonces étaient perdues quand plus d'une page arrivait entre deux scans.
2. Un keyword ajouté à chaud n'était pas réellement cherché.
3. Les keywords ajoutés depuis le dashboard disparaissaient au redémarrage.
"""

import asyncio
import time

import pytest

from mercari_sniper.backends.base import SearchPage, SearchQuery
from mercari_sniper.buffer import RecentBuffer
from mercari_sniper.config import Config, SourceConfig
from mercari_sniper.engine import SniperEngine
from mercari_sniper.events import EventBus
from mercari_sniper.matching import broad_root, covers
from mercari_sniper.models import Listing
from mercari_sniper.store import Store


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


class PagedBackend:
    """Backend paginé : sert des pages successives via page_token."""

    name = "paged"

    def __init__(self, pages: list[list[Listing]]):
        self.pages = pages
        self.requested_tokens: list[str] = []

    async def search(self, query: SearchQuery) -> SearchPage:
        self.requested_tokens.append(query.page_token)
        index = int(query.page_token) if query.page_token.isdigit() else 0
        if index >= len(self.pages):
            return SearchPage(items=[])
        next_token = str(index + 1) if index + 1 < len(self.pages) else ""
        return SearchPage(items=list(self.pages[index]), next_page_token=next_token)

    async def aclose(self):
        return None


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
        return {}


@pytest.fixture
async def context(tmp_path):
    config = Config()
    config.storage.database = str(tmp_path / "test.db")
    config.path = tmp_path / "config.yaml"
    config.notify.console = False
    config.poll.warmup = False
    store = Store(config.storage.database)
    await store.open()
    yield config, store, EventBus()
    await store.close()


# ══════════════════════════════════════════════════════════════════════════
class TestCoverageHelpers:
    def test_root_covers_refinement(self):
        assert covers("ナイキ", "ナイキ トレイル")
        assert covers("nike", "nike acg vest")

    def test_unrelated_query_does_not_cover(self):
        assert not covers("ナイキ", "アディダス ジャージ")
        assert not covers("nike", "adidas samba")

    def test_narrower_source_does_not_cover_broader_keyword(self):
        # Chercher « nike acg » ne ramène pas tout « nike ».
        assert not covers("nike acg", "nike")

    def test_spacing_variants_are_equivalent(self):
        # Les titres japonais s'écrivent avec ou sans espace.
        assert covers("ナイキ", "ナイキトレイル")

    def test_empty_query_covers_nothing(self):
        assert not covers("", "nike")

    def test_broad_root_is_first_term(self):
        assert broad_root("ナイキ トレイル ベスト") == "ナイキ"
        assert broad_root("  nike   acg ") == "nike"
        assert broad_root("") == ""


# ══════════════════════════════════════════════════════════════════════════
class TestRecentBuffer:
    def test_deduplicates(self):
        buffer = RecentBuffer()
        assert buffer.add(make_listing("m1", "a"))
        assert not buffer.add(make_listing("m1", "a"))
        assert len(buffer) == 1

    def test_bounded_by_size(self):
        buffer = RecentBuffer(max_items=5)
        for i in range(20):
            buffer.add(make_listing(f"m{i}", "x"))
        assert len(buffer) == 5

    def test_evicts_by_age(self):
        buffer = RecentBuffer(window_seconds=60)
        stale = make_listing("old", "x")
        stale.detected_at = time.time() - 600
        buffer.add(stale)
        buffer.add(make_listing("new", "x"))
        assert [item.id for item in buffer] == ["new"]

    def test_snapshot_is_newest_first(self):
        buffer = RecentBuffer()
        for i in range(3):
            buffer.add(make_listing(f"m{i}", "x"))
        assert [item.id for item in buffer.snapshot()] == ["m2", "m1", "m0"]


# ══════════════════════════════════════════════════════════════════════════
class TestOverflowRecovery:
    """Bug 1 : une page entièrement inédite signale des annonces perdues."""

    async def test_full_page_triggers_catch_up(self, context):
        config, store, bus = context
        config.keywords = ["nike"]
        config.sources = [SourceConfig(query="nike", page_size=2)]

        known = make_listing("m_known", "Nike known")
        backend = PagedBackend([
            [make_listing("m1", "Nike A"), make_listing("m2", "Nike B")],
            [make_listing("m3", "Nike C"), known],
        ])
        engine = SniperEngine(config, backend, store, bus)
        state = engine._sources["nike"]
        state.warmed_up = True
        engine._remember(known.id)

        await engine._poll_source(state)

        # La 2e page a été demandée : le trou est rattrapé.
        assert backend.requested_tokens == ["", "1"]
        assert state.overflows == 1
        # m3, qui aurait été perdu, est bien remonté.
        assert "m3" in engine._seen

    async def test_partial_page_does_not_paginate(self, context):
        config, store, bus = context
        config.keywords = ["nike"]
        config.sources = [SourceConfig(query="nike", page_size=2)]

        known = make_listing("m_known", "Nike known")
        backend = PagedBackend([[make_listing("m1", "Nike A"), known]])
        engine = SniperEngine(config, backend, store, bus)
        state = engine._sources["nike"]
        state.warmed_up = True
        engine._remember(known.id)

        await engine._poll_source(state)

        assert backend.requested_tokens == [""]   # une seule requête
        assert state.overflows == 0

    async def test_catch_up_tightens_the_interval(self, context):
        config, store, bus = context
        config.keywords = ["nike"]
        config.sources = [SourceConfig(query="nike", page_size=2)]
        backend = PagedBackend([
            [make_listing("m1", "Nike A"), make_listing("m2", "Nike B")],
            [make_listing("m3", "Nike C")],
        ])
        engine = SniperEngine(config, backend, store, bus)
        state = engine._sources["nike"]
        state.warmed_up = True
        before = state.interval

        await engine._poll_source(state)
        assert state.interval < before

    async def test_warmup_page_never_triggers_catch_up(self, context):
        """Au tout premier scan, tout est inédit : ce n'est pas un débordement."""
        config, store, bus = context
        config.keywords = ["nike"]
        config.sources = [SourceConfig(query="nike", page_size=2)]
        config.poll.warmup = True
        backend = PagedBackend([
            [make_listing("m1", "Nike A"), make_listing("m2", "Nike B")],
            [make_listing("m3", "Nike C")],
        ])
        engine = SniperEngine(config, backend, store, bus)

        await engine._poll_source(engine._sources["nike"])
        assert backend.requested_tokens == [""]
        assert engine._sources["nike"].overflows == 0


# ══════════════════════════════════════════════════════════════════════════
class TestKeywordIsActuallySearched:
    """Bug 2 : un keyword ajouté à chaud doit être cherché ET rattraper le passé."""

    async def test_uncovered_keyword_creates_a_source(self, context):
        config, store, bus = context
        config.keywords = ["nike acg"]
        config.sources = [SourceConfig(query="nike", auto=True)]
        engine = SniperEngine(config, PagedBackend([]), store, bus)

        result = await engine.add_keyword("アディダス テレックス")

        assert result["added"]
        assert result["source_created"] == "アディダス テレックス"
        assert "アディダス テレックス" in engine._sources

    async def test_covered_keyword_reuses_existing_source(self, context):
        config, store, bus = context
        config.keywords = ["nike acg"]
        config.sources = [SourceConfig(query="nike", auto=True)]
        engine = SniperEngine(config, PagedBackend([]), store, bus)

        result = await engine.add_keyword("nike phenom elite")

        assert result["added"]
        assert result["source_created"] is None
        assert list(engine._sources) == ["nike"]

    async def test_backfill_finds_already_scanned_listings(self, context):
        """Le cœur du bug : ce qui a déjà été scanné doit rester rattrapable."""
        config, store, bus = context
        config.keywords = ["nike acg"]
        config.sources = [SourceConfig(query="nike")]

        # Une annonce Adidas passe pendant qu'aucun keyword ne la vise.
        backend = PagedBackend([[make_listing("m1", "Adidas Terrex Jacket")]])
        notifier = CaptureNotifier()
        engine = SniperEngine(config, backend, store, bus, [notifier])
        await engine._poll_source(engine._sources["nike"])

        assert notifier.items == []            # rien ne matchait
        assert len(engine.buffer) == 1         # mais elle est mémorisée

        result = await engine.add_keyword("adidas terrex")

        assert result["backfilled"] == 1
        assert engine.total_hits == 1
        assert engine.feed[0]["title"] == "Adidas Terrex Jacket"
        assert engine.feed[0]["backfill"] is True

    async def test_backfill_does_not_spam_discord(self, context):
        """Le rattrapage s'affiche, mais ne réveille pas les notificateurs."""
        config, store, bus = context
        config.keywords = ["nike acg"]
        config.sources = [SourceConfig(query="nike")]
        backend = PagedBackend([[make_listing("m1", "Adidas Terrex Jacket")]])
        notifier = CaptureNotifier()
        engine = SniperEngine(config, backend, store, bus, [notifier])
        await engine._poll_source(engine._sources["nike"])

        await engine.add_keyword("adidas terrex")
        assert notifier.items == []

    async def test_backfill_respects_max_age(self, context):
        config, store, bus = context
        config.keywords = ["nike acg"]
        config.sources = [SourceConfig(query="nike")]
        config.filters.max_age_seconds = 300

        old = make_listing("m1", "Adidas Terrex", created=time.time() - 5000)
        backend = PagedBackend([[old]])
        engine = SniperEngine(config, backend, store, bus)
        await engine._poll_source(engine._sources["nike"])

        result = await engine.add_keyword("adidas terrex")
        assert result["backfilled"] == 0

    async def test_no_duplicate_emission(self, context):
        config, store, bus = context
        config.keywords = ["adidas"]
        config.sources = [SourceConfig(query="adidas")]
        backend = PagedBackend([[make_listing("m1", "Adidas Terrex Jacket")]])
        engine = SniperEngine(config, backend, store, bus)
        await engine._poll_source(engine._sources["adidas"])
        assert engine.total_hits == 1

        # Un second keyword touchant la même annonce ne la duplique pas.
        result = await engine.add_keyword("adidas terrex")
        assert result["backfilled"] == 0
        assert engine.total_hits == 1

    async def test_removing_keyword_drops_its_auto_source(self, context):
        config, store, bus = context
        engine = SniperEngine(config, PagedBackend([]), store, bus)

        await engine.add_keyword("アディダス テレックス")
        assert "アディダス テレックス" in engine._sources

        await engine.remove_keyword("アディダス テレックス")
        assert "アディダス" not in engine._sources

    async def test_manual_source_survives_keyword_removal(self, context):
        config, store, bus = context
        config.sources = [SourceConfig(query="nike", auto=False)]
        engine = SniperEngine(config, PagedBackend([]), store, bus)

        await engine.add_keyword("nike acg")
        await engine.remove_keyword("nike acg")
        assert "nike" in engine._sources   # déclarée à la main : on n'y touche pas

    async def test_new_source_starts_polling(self, context):
        """La source créée doit vraiment être interrogée, pas juste enregistrée."""
        config, store, bus = context
        backend = PagedBackend([[make_listing("m1", "Adidas Terrex")]] * 20)
        engine = SniperEngine(config, backend, store, bus)
        await engine.start()
        try:
            await engine.add_keyword("adidas terrex")
            await asyncio.sleep(0.4)
            assert engine._sources["adidas terrex"].polls >= 1
        finally:
            await engine.stop()


# ══════════════════════════════════════════════════════════════════════════
class TestPersistence:
    """Bug 3 : les keywords ajoutés à chaud doivent survivre au redémarrage."""

    async def test_keyword_add_is_saved_to_disk(self, context):
        config, store, bus = context
        engine = SniperEngine(config, PagedBackend([]), store, bus)

        await engine.add_keyword("nike acg")
        await asyncio.sleep(1.8)   # au-delà du debounce de sauvegarde

        assert config.path.exists()
        reloaded = Config.load(config.path)
        assert "nike acg" in reloaded.keywords

    async def test_keyword_removal_is_saved(self, context):
        config, store, bus = context
        engine = SniperEngine(config, PagedBackend([]), store, bus)

        await engine.add_keyword("nike acg")
        await engine.remove_keyword("nike acg")
        await asyncio.sleep(1.8)

        reloaded = Config.load(config.path)
        assert reloaded.keywords == []

    async def test_rapid_edits_collapse_into_one_write(self, context):
        config, store, bus = context
        engine = SniperEngine(config, PagedBackend([]), store, bus)

        for index in range(5):
            await engine.add_keyword(f"keyword{index}")
        await asyncio.sleep(1.8)

        reloaded = Config.load(config.path)
        assert len(reloaded.keywords) == 5


# ══════════════════════════════════════════════════════════════════════════
class TestMetrics:
    async def test_coverage_flags_uncovered_keywords(self, context):
        config, store, bus = context
        config.keywords = ["adidas samba"]
        config.sources = [SourceConfig(query="nike")]
        engine = SniperEngine(config, PagedBackend([]), store, bus)

        assert engine.coverage()["uncovered_keywords"] == ["adidas samba"]

    async def test_coverage_detects_budget_saturation(self, context):
        config, store, bus = context
        config.poll.global_rate_limit = 1.0
        config.sources = [
            SourceConfig(query=f"q{i}", interval=1.0) for i in range(10)
        ]
        engine = SniperEngine(config, PagedBackend([]), store, bus)

        coverage = engine.coverage()
        assert coverage["demand_per_second"] > coverage["budget_per_second"]
        assert coverage["saturated"] is True

    async def test_activity_buckets_hits_per_minute(self, context):
        config, store, bus = context
        engine = SniperEngine(config, PagedBackend([]), store, bus)
        now = time.time()
        engine._hit_times.extend([now, now, now - 120])

        activity = engine.activity(minutes=5)
        assert len(activity) == 5
        assert activity[-1] == 2      # minute courante
        assert activity[-3] == 1      # il y a deux minutes

    async def test_keyword_stats_rank_by_hits(self, context):
        config, store, bus = context
        config.keywords = ["a", "b"]
        config.sources = [SourceConfig(query="a")]
        engine = SniperEngine(config, PagedBackend([]), store, bus)
        engine._keyword_hits = {"b": 7, "a": 2}

        stats = engine.keyword_stats()
        assert [entry["keyword"] for entry in stats] == ["b", "a"]
        assert stats[1]["covered"] is True     # la source "a" couvre "a"

    async def test_source_health_labels(self, context):
        config, store, bus = context
        config.sources = [SourceConfig(query="nike")]
        engine = SniperEngine(config, PagedBackend([]), store, bus)
        state = engine._sources["nike"]

        assert state.health == "starting"
        state.polls = 1
        assert state.health == "ok"
        state.fill_ratio = 0.9
        assert state.health == "saturated"
        state.consecutive_errors = 5
        assert state.health == "error"
        state.paused = True
        assert state.health == "paused"
