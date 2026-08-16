"""Persistance SQLite."""

import time

import pytest

from mercari_sniper.models import Listing
from mercari_sniper.store import Store


@pytest.fixture
async def store(tmp_path):
    store = Store(tmp_path / "test.db", retention_days=30)
    await store.open()
    yield store
    await store.close()


def make_listing(item_id="m1", title="Nike Trail", matched=None):
    now = time.time()
    return Listing(
        id=item_id,
        title=title,
        price=8900,
        url=f"https://jp.mercari.com/item/{item_id}",
        matched=matched or ["nike trail"],
        rarity="RARE",
        created=int(now) - 5,
        detected_at=now,
    )


class TestPersistence:
    async def test_queue_then_flush_writes_rows(self, store):
        store.queue(make_listing("m1"))
        store.queue(make_listing("m2"))
        await store.flush()

        rows = await store.recent(10)
        assert {row["id"] for row in rows} == {"m1", "m2"}

    async def test_queue_alone_writes_nothing(self, store):
        store.queue(make_listing("m1"))
        assert await store.recent(10) == []

    async def test_matched_roundtrips_as_list(self, store):
        store.queue(make_listing("m1", matched=["nike trail", "ナイキ トレイル"]))
        await store.flush()
        rows = await store.recent(1)
        assert rows[0]["matched"] == ["nike trail", "ナイキ トレイル"]

    async def test_duplicate_id_is_ignored(self, store):
        store.queue(make_listing("m1"))
        await store.flush()
        store.queue(make_listing("m1", title="Titre différent"))
        await store.flush()

        rows = await store.recent(10)
        assert len(rows) == 1
        assert rows[0]["title"] == "Nike Trail"   # la première écriture gagne

    async def test_latency_is_persisted(self, store):
        listing = Listing(
            id="m1", title="t", price=1, url="u", created=1000, detected_at=1002.5
        )
        store.queue(listing)
        await store.flush()
        assert (await store.recent(1))[0]["latency_ms"] == 2500

    async def test_ordered_newest_first(self, store):
        for i in range(5):
            listing = make_listing(f"m{i}")
            listing.detected_at = 1000 + i
            store.queue(listing)
        await store.flush()

        rows = await store.recent(5)
        assert [row["id"] for row in rows] == ["m4", "m3", "m2", "m1", "m0"]

    async def test_limit_is_respected(self, store):
        for i in range(10):
            store.queue(make_listing(f"m{i}"))
        await store.flush()
        assert len(await store.recent(3)) == 3


class TestSeenCache:
    async def test_mark_and_reload(self, store):
        await store.mark_seen_bulk(["m1", "m2", "m3"])
        assert await store.load_seen_ids() == {"m1", "m2", "m3"}

    async def test_flush_also_marks_seen(self, store):
        store.queue(make_listing("m1"))
        await store.flush()
        assert "m1" in await store.load_seen_ids()

    async def test_empty_bulk_is_noop(self, store):
        await store.mark_seen_bulk([])
        assert await store.load_seen_ids() == set()

    async def test_marking_twice_is_idempotent(self, store):
        await store.mark_seen_bulk(["m1"])
        await store.mark_seen_bulk(["m1"])
        assert await store.load_seen_ids() == {"m1"}


class TestFiltersAndStats:
    async def test_filter_by_keyword(self, store):
        store.queue(make_listing("m1", matched=["nike trail"]))
        store.queue(make_listing("m2", matched=["under armour storm"]))
        await store.flush()

        rows = await store.recent(10, keyword="nike trail")
        assert [row["id"] for row in rows] == ["m1"]

    async def test_stats(self, store):
        store.queue(make_listing("m1"))
        store.queue(make_listing("m2"))
        await store.flush()

        stats = await store.stats()
        assert stats["total_listings"] == 2
        assert stats["listings_24h"] == 2
        assert stats["seen_cache"] == 2

    async def test_stats_on_empty_database(self, store):
        stats = await store.stats()
        assert stats["total_listings"] == 0
        assert stats["avg_latency_ms"] == 0


class TestMaintenance:
    async def test_prune_removes_old_rows_only(self, store):
        recent = make_listing("m_recent")
        old = make_listing("m_old")
        old.detected_at = time.time() - 60 * 86400
        store.queue(recent)
        store.queue(old)
        await store.flush()

        deleted = await store.prune()
        assert deleted == 1
        assert [row["id"] for row in await store.recent(10)] == ["m_recent"]

    async def test_reopen_keeps_data(self, tmp_path):
        path = tmp_path / "persist.db"
        store = Store(path)
        await store.open()
        store.queue(make_listing("m1"))
        await store.flush()
        await store.close()

        again = Store(path)
        await again.open()
        assert len(await again.recent(10)) == 1
        await again.close()

    async def test_close_flushes_pending(self, tmp_path):
        path = tmp_path / "flush.db"
        store = Store(path)
        await store.open()
        store.queue(make_listing("m1"))
        await store.close()      # doit vider la file avant de fermer

        again = Store(path)
        await again.open()
        assert len(await again.recent(10)) == 1
        await again.close()
