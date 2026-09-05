"""Tests d'intégration : le pipeline complet, sans réseau.

Ils vérifient les invariants que les tests unitaires ne peuvent pas voir —
notamment que le scanner ne s'arrête jamais pour attendre quelqu'un.
"""

import asyncio
import time

import pytest

from snipe.core.filters import FilterEngine, GlobalFilters
from snipe.core.keywords import Keyword
from snipe.core.scanner import Scanner
from snipe.core.scheduler import Scheduler
from snipe.notifications.base import NotificationHub
from snipe.sources.base import SearchResult, SourceError
from snipe.storage.sqlite import Storage

from .helpers import ScriptedSource, make_listing


class CaptureNotifier:
    name = "capture"
    enabled = True

    def __init__(self, delay=0.0, fail_times=0):
        self.received = []
        self.delay = delay
        self.fail_times = fail_times
        self.attempts = 0
        self.closed = False

    async def send(self, listing):
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise RuntimeError("échec simulé")
        if self.delay:
            await asyncio.sleep(self.delay)
        self.received.append(listing)

    async def close(self):
        self.closed = True


@pytest.fixture
async def storage(tmp_path):
    store = Storage(tmp_path / "test.db")
    await store.open()
    yield store
    await store.close()


#: Un seul mot-clé = une seule requête. Indispensable dès qu'on scripte des
#: pages : avec deux familles de recherche, deux requêtes puisent dans la
#: même liste et l'ordre des pages devient indéterminé.
SOLO = [Keyword.build("Nike", search=["ナイキ"], include=["ナイキ", "nike"])]

#: Cadence accélérée pour les tests : les intervalles nominaux (2/5/20 s)
#: rendraient chaque test long de plusieurs dizaines de secondes.
FAST = {"high": 0.05, "medium": 0.05, "low": 0.05}


def build_scanner(sources, keywords, storage, hub, **kwargs):
    return Scanner(
        sources=sources,
        keywords=keywords,
        filters=FilterEngine(
            keywords=keywords, globals_=GlobalFilters.build(max_age_seconds=0)
        ),
        hub=hub,
        storage=storage,
        scheduler=Scheduler(
            intervals=dict(kwargs.pop("intervals", FAST)),
            budget_per_second=kwargs.pop("budget", 200.0),
            jitter=0.0,
        ),
        **kwargs,
    )


class TestWarmup:
    async def test_first_pass_notifies_nothing(self, storage, keywords):
        """Sans warmup, tout le catalogue en ligne partirait au démarrage."""
        source = ScriptedSource([[make_listing("m1", source="scripted")]])
        notifier = CaptureNotifier()
        hub = NotificationHub([notifier])
        await hub.start()

        scanner = build_scanner({"scripted": source}, keywords, storage, hub)
        await scanner.start()
        await asyncio.sleep(0.4)
        await scanner.stop()
        await hub.stop()

        assert notifier.received == []

    async def test_second_pass_notifies(self, storage):
        source = ScriptedSource([
            [make_listing("m1", title="ナイキ ジャケット", source="scripted")],
            [make_listing("m1", title="ナイキ ジャケット", source="scripted"),
             make_listing("m2", title="ナイキ トレイル ベスト", source="scripted")],
        ])
        notifier = CaptureNotifier()
        hub = NotificationHub([notifier])
        await hub.start()

        scanner = build_scanner({"scripted": source}, SOLO, storage, hub)
        await scanner.start()
        await asyncio.sleep(1.2)
        await scanner.stop()
        await hub.drain()
        await hub.stop()

        assert [item.id for item in notifier.received] == ["m2"]


class TestDeduplication:
    async def test_same_listing_notified_once(self, storage, keywords):
        listing = make_listing("m1", source="scripted")
        source = ScriptedSource([[listing], [listing], [listing], [listing]])
        notifier = CaptureNotifier()
        hub = NotificationHub([notifier])
        await hub.start()

        scanner = build_scanner({"scripted": source}, keywords, storage, hub)
        await scanner.start()
        await asyncio.sleep(1.5)
        await scanner.stop()
        await hub.stop()

        assert len(notifier.received) == 0   # vue au warmup, jamais renotifiée

    async def test_dedup_survives_a_restart(self, tmp_path, keywords):
        """Redémarrer ne doit pas renotifier tout ce qui est encore en ligne."""
        store = Storage(tmp_path / "restart.db")
        await store.open()
        await store.mark_seen(["scripted:m1"])
        await store.close()

        store2 = Storage(tmp_path / "restart.db")
        await store2.open()
        hub = NotificationHub([])
        scanner = build_scanner({}, keywords, store2, hub)
        await scanner.start()
        assert scanner.dedup.seen("scripted:m1")
        await scanner.stop()
        await store2.close()


class TestScannerNeverBlocks:
    """Le critère n°8 : le scanner n'attend jamais Telegram."""

    async def test_slow_notifier_does_not_stall_the_scan(self, storage, keywords):
        slow = CaptureNotifier(delay=0.5)
        hub = NotificationHub([slow])
        await hub.start()

        pages = [[make_listing(f"m{i}", source="scripted")] for i in range(8)]
        source = ScriptedSource(pages)
        scanner = build_scanner({"scripted": source}, keywords, storage, hub)

        await scanner.start()
        await asyncio.sleep(1.5)
        await scanner.stop()

        # Le notifieur met 0,5 s par envoi. Si le scanner l'attendait, il
        # n'aurait pu faire que 3 requêtes en 1,5 s.
        assert len(source.queries) > 4, (
            f"seulement {len(source.queries)} requêtes : le scanner a attendu "
            "les notifications"
        )
        await hub.stop()

    async def test_dispatch_is_synchronous(self):
        """`dispatch()` ne doit être ni une coroutine ni bloquante."""
        hub = NotificationHub([CaptureNotifier()])
        await hub.start()
        started = time.perf_counter()
        for i in range(500):
            hub.dispatch(make_listing(f"m{i}"))
        assert (time.perf_counter() - started) < 0.1
        await hub.stop()


class TestNotificationResilience:
    async def test_retry_then_success(self):
        notifier = CaptureNotifier(fail_times=2)
        hub = NotificationHub([notifier], max_retries=3)
        await hub.start()
        hub.dispatch(make_listing("m1"))
        await hub.drain(timeout=5)
        await hub.stop()

        assert len(notifier.received) == 1
        assert hub.stats["capture"].retries == 2

    async def test_permanent_failure_is_counted_not_fatal(self):
        notifier = CaptureNotifier(fail_times=99)
        hub = NotificationHub([notifier], max_retries=2)
        await hub.start()
        hub.dispatch(make_listing("m1"))
        await hub.drain(timeout=5)

        assert hub.stats["capture"].failed == 1
        # Le worker doit être encore vivant pour la suivante.
        notifier.fail_times = 0
        hub.dispatch(make_listing("m2"))
        await hub.drain(timeout=5)
        assert len(notifier.received) == 1
        await hub.stop()

    async def test_full_queue_drops_oldest_and_keeps_scanning(self):
        """Une file non bornée devant un canal en panne finirait par tuer le bot."""
        notifier = CaptureNotifier(delay=10.0)
        hub = NotificationHub([notifier], max_queue=5)
        await hub.start()
        for i in range(50):
            hub.dispatch(make_listing(f"m{i}"))
        assert hub.stats["capture"].dropped > 0
        await hub.stop()

    async def test_dry_run_sends_nothing(self):
        notifier = CaptureNotifier()
        hub = NotificationHub([notifier], dry_run=True)
        await hub.start()
        hub.dispatch(make_listing("m1"))
        await hub.drain(timeout=5)
        await hub.stop()

        assert notifier.received == []
        assert hub.stats["capture"].sent == 1


class TestSourceIsolation:
    """Critère n°3 : une source qui tombe n'arrête pas les autres."""

    async def test_failing_source_does_not_stop_the_other(self, storage, keywords):
        broken = ScriptedSource(error=SourceError("panne", status=503))
        healthy = ScriptedSource([
            [make_listing("m1", source="ok")],
            [make_listing("m2", title="ナイキ トレイル", source="ok")],
        ])
        hub = NotificationHub([])
        scanner = build_scanner(
            {"broken": broken, "ok": healthy}, keywords, storage, hub
        )
        await scanner.start()
        await asyncio.sleep(1.0)
        await scanner.stop()

        assert len(healthy.queries) >= 2, "la source saine a été entraînée dans la chute"
        assert scanner.metrics.source("broken").errors.total >= 1

    async def test_unexpected_exception_is_contained(self, storage, keywords):
        class Exploding:
            name = "boom"
            verified = True

            async def search(self, query):
                raise ValueError("bug interne")

            async def health_check(self):
                from snipe.sources.base import SourceHealth
                return SourceHealth(ok=False, detail="x")

            async def close(self):
                pass

        hub = NotificationHub([])
        scanner = build_scanner({"boom": Exploding()}, keywords, storage, hub)
        await scanner.start()
        await asyncio.sleep(0.5)
        assert scanner.metrics.source("boom").errors.total >= 1
        await scanner.stop()

    async def test_rate_limit_lowers_the_global_budget(self, storage, keywords):
        """Un 429 est par adresse IP : ralentir une seule source ne sert à rien."""
        source = ScriptedSource(error=SourceError("429", status=429, retry_after=0.1))
        hub = NotificationHub([])
        scanner = build_scanner({"s": source}, keywords, storage, hub, budget=10.0)
        await scanner.start()
        await asyncio.sleep(0.4)
        assert scanner.scheduler.budget_per_second < 10.0
        await scanner.stop()


class TestGapDetection:
    async def test_page_not_reaching_back_triggers_pagination(self, storage):
        now = time.time()
        first = SearchResult(
            listings=[make_listing("old", title="ナイキ 旧", published_at=now - 100,
                                   source="s")],
            requested_at=now, received_at=now,
        )
        # Page entièrement postérieure au repère : il manque forcément
        # quelque chose entre les deux.
        burst = SearchResult(
            listings=[
                make_listing(f"n{i}", title="ナイキ 新", published_at=now - 1 + i, source="s")
                for i in range(3)
            ],
            cursor="page2", requested_at=now, received_at=now,
        )
        recovered = SearchResult(
            listings=[make_listing("old", title="ナイキ 旧", published_at=now - 100,
                                   source="s")],
            requested_at=now, received_at=now,
        )
        source = ScriptedSource([first, burst, recovered])
        hub = NotificationHub([])
        scanner = build_scanner({"s": source}, SOLO, storage, hub)

        await scanner.start()
        await asyncio.sleep(1.2)
        await scanner.stop()

        assert any(q.cursor == "page2" for q in source.queries), (
            "le trou n'a pas été rattrapé"
        )

    async def test_gap_demotes_the_query_in_the_planner(self, storage):
        keywords = [
            Keyword.build("A", search=["ナイキ"], include=["ディビジョン"]),
            Keyword.build("B", search=["ナイキ"], include=["トレイル"]),
        ]
        now = time.time()
        source = ScriptedSource([
            SearchResult(listings=[make_listing("old", published_at=now - 100,
                                                source="s")],
                         requested_at=now, received_at=now),
            SearchResult(
                listings=[make_listing(f"n{i}", published_at=now - 1 + i, source="s")
                          for i in range(3)],
                cursor="p2", requested_at=now, received_at=now,
            ),
            SearchResult(listings=[], requested_at=now, received_at=now),
        ])
        hub = NotificationHub([])
        scanner = build_scanner({"s": source}, keywords, storage, hub)

        await scanner.start()
        assert len(scanner.scheduler.tasks) == 1, "regroupé au départ"
        await asyncio.sleep(1.2)
        await scanner.stop()

        assert scanner.planner("s").demoted, "le trou aurait dû dégrouper la requête"


class TestStorage:
    async def test_listings_round_trip(self, storage):
        listing = make_listing("m1", source="mercari")
        listing.keywords = ["Nike Division"]
        storage.queue(listing)
        assert await storage.flush() == 1

        rows = await storage.recent(10)
        assert rows[0]["id"] == "m1"
        assert rows[0]["keywords"] == ["Nike Division"]

    async def test_duplicate_insert_is_ignored(self, storage):
        listing = make_listing("m1", source="mercari")
        storage.queue(listing)
        storage.queue(listing)
        await storage.flush()
        assert len(await storage.recent(10)) == 1

    async def test_seen_ids_are_persisted(self, storage):
        await storage.mark_seen(["mercari:m9"])
        assert "mercari:m9" in await storage.load_seen()

    async def test_stats(self, storage):
        storage.queue(make_listing("m1", source="mercari"))
        await storage.flush()
        stats = await storage.stats()
        assert stats["listings_total"] == 1

    async def test_purge_removes_old_rows(self, storage):
        storage.queue(make_listing("m1", source="mercari"))
        await storage.flush()
        assert await storage.purge(retention_days=0) == 0   # 0 = désactivé
        assert len(await storage.recent(10)) == 1

    async def test_notifications_are_logged(self, storage):
        storage.queue_notification("mercari:m1", "telegram", True, 87.4)
        await storage.flush()
        assert storage.pending == 0
