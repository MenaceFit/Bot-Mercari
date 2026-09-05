"""Normalisation, mots-clés, filtrage, déduplication, ordonnancement."""

import time

import pytest

from snipe.core.deduplicator import Deduplicator
from snipe.core.filters import FilterEngine, GlobalFilters
from snipe.core.keywords import Keyword
from snipe.core.metrics import Histogram, Metrics
from snipe.core.normalizer import (
    contains_term,
    covers,
    normalize_text,
    terms_of,
)
from snipe.core.scheduler import Scheduler

from .helpers import make_listing


class TestNormalizer:
    def test_nfkc_unifies_halfwidth_katakana(self):
        """ﾅｲｷ et ナイキ sont la même chose pour un vendeur japonais."""
        assert normalize_text("ﾅｲｷ") == normalize_text("ナイキ")

    def test_fullwidth_digits_and_latin(self):
        assert normalize_text("ＮＩＫＥ ２７cm") == "nike 27cm"

    def test_punctuation_becomes_space(self):
        assert normalize_text("Nike/ACG・Jacket") == "nike acg jacket"

    def test_case_folding(self):
        assert normalize_text("NIKE") == normalize_text("nike")

    def test_empty_input(self):
        assert normalize_text("") == ""


class TestTermMatching:
    """Latin ancré en début de mot, japonais en sous-chaîne."""

    @pytest.mark.parametrize("title,expected", [
        ("nike air max", True),
        ("airmax 90", True),
        ("repair kit", False),
        ("hair dryer", False),
        ("chair", False),
    ])
    def test_latin_prefix_anchor(self, title, expected):
        assert contains_term(normalize_text(title), "air") is expected

    def test_brand_matches_sub_labels(self):
        assert contains_term(normalize_text("nikelab acg"), "nike")

    def test_brand_not_matched_inside_another_word(self):
        assert not contains_term(normalize_text("unlike new"), "nike")

    def test_japanese_is_substring(self):
        assert contains_term(normalize_text("ナイキエアマックス"), "ナイキ")
        assert not contains_term(normalize_text("アディダス"), "ナイキ")

    def test_covers_relation(self):
        assert covers("ナイキ", "ナイキ ディビジョン")
        assert not covers("ナイキ ディビジョン", "ナイキ")
        assert covers("nike", "nike acg")

    def test_terms_of_splits(self):
        assert terms_of("Nike Division") == ("nike", "division")


class TestKeyword:
    def test_include_alternatives_are_or(self):
        """« ディビジョン » OU « division » — exiger les deux ne matcherait rien."""
        kw = Keyword.build("Nike Division", include=["ディビジョン", "division"])
        assert kw.matches(normalize_text("ナイキ ディビジョン ジャケット"))
        assert kw.matches(normalize_text("NIKE DIVISION JACKET"))

    def test_multi_word_alternative_is_and(self):
        kw = Keyword.build("UA", include=["under armour"])
        assert kw.matches(normalize_text("under armour hoodie"))
        assert not kw.matches(normalize_text("under the bridge"))

    def test_exclude_wins_over_include(self):
        kw = Keyword.build("Nike Trail", include=["trail"], exclude=["shoes"])
        assert kw.matches(normalize_text("nike trail jacket"))
        assert not kw.matches(normalize_text("nike trail shoes"))

    def test_price_bounds(self):
        kw = Keyword.build("x", include=["nike"], min_price=5000, max_price=20000)
        assert not kw.matches(normalize_text("nike"), 3000)
        assert kw.matches(normalize_text("nike"), 8900)
        assert not kw.matches(normalize_text("nike"), 50000)

    def test_source_restriction(self):
        kw = Keyword.build("x", include=["nike"], sources=["mercari"])
        assert kw.matches(normalize_text("nike"), 0, "mercari")
        assert not kw.matches(normalize_text("nike"), 0, "yahoo_auction")

    def test_search_defaults_to_the_name(self):
        assert Keyword.build("Nike Division").search == ("Nike Division",)

    def test_disabled_never_matches(self):
        kw = Keyword.build("x", include=["nike"], enabled=False)
        assert not kw.matches(normalize_text("nike"))

    def test_invalid_regex_is_ignored_not_fatal(self):
        """Une faute de frappe dans le YAML ne doit pas empêcher le démarrage."""
        kw = Keyword.build("x", include=["nike"], exclude_regex=["([unclosed"])
        assert kw.exclude_regex == ()
        assert kw.matches(normalize_text("nike"))

    def test_regex_filters_apply(self):
        kw = Keyword.build("x", include=["nike"], exclude_regex=[r"\d{2}cm"])
        assert kw.matches(normalize_text("nike jacket"))
        assert not kw.matches(normalize_text("nike sneakers 27cm"))


class TestFilterEngine:
    def _engine(self, keywords, **globals_):
        return FilterEngine(
            keywords=keywords, globals_=GlobalFilters.build(**globals_)
        )

    def test_matches_the_right_keyword(self, keywords):
        engine = self._engine(keywords)
        listing = make_listing(title="ナイキ ディビジョン ジャケット")
        assert engine.match(listing, time.time()) == ["Nike Division"]

    def test_allowed_restricts_to_the_serving_query(self, keywords):
        """Sans cette restriction, un mot-clé contamine les autres marques.

        « Nike Trail » n'exige que « トレイル ». Une annonce Under Armour
        ramenée par la requête Under Armour ne doit pas lui être attribuée.
        """
        engine = self._engine(keywords)
        listing = make_listing(title="アンダーアーマー トレイル キャップ")

        assert "Nike Trail" in engine.match(listing, time.time())
        assert engine.match(
            listing, time.time(), allowed=frozenset({"Under Armour"})
        ) == ["Under Armour"]

    def test_global_exclusion(self, keywords):
        engine = self._engine(keywords, exclude=["香水", "空箱"])
        listing = make_listing(title="ナイキ ディビジョン 空箱 のみ")
        assert engine.match(listing, time.time()) == []
        assert engine.drops["exclu"] == 1

    def test_price_bounds_are_counted(self, keywords):
        engine = self._engine(keywords, min_price=10000)
        assert engine.match(make_listing(price=5000), time.time()) == []
        assert engine.drops["prix"] == 1

    def test_old_listing_is_dropped(self, keywords):
        engine = self._engine(keywords, max_age_seconds=60)
        now = time.time()
        listing = make_listing(published_at=now - 3600)
        assert engine.match(listing, now) == []
        assert engine.drops["trop ancienne"] == 1

    def test_undated_listing_survives_the_age_filter(self, keywords):
        """Une source sans date de publication ne doit pas tout perdre."""
        engine = self._engine(keywords, max_age_seconds=60)
        listing = make_listing(published_at=0.0)
        assert engine.match(listing, time.time()) == ["Nike Division"]

    def test_no_keyword_is_counted(self, keywords):
        engine = self._engine(keywords)
        assert engine.match(make_listing(title="chaise en bois"), time.time()) == []
        assert engine.drops["aucun mot-clé"] == 1

    def test_reindex_after_keyword_change(self, keywords):
        engine = self._engine(keywords)
        engine.set_keywords([Keyword.build("Neuf", include=["gyakusou"])])
        assert engine.match(
            make_listing(title="nike gyakusou short"), time.time()
        ) == ["Neuf"]


class TestDeduplicator:
    def test_first_add_is_new(self):
        dedup = Deduplicator()
        assert dedup.add("mercari:m1") is True
        assert dedup.add("mercari:m1") is False

    def test_same_id_different_source_are_distinct(self):
        """La clé est source+id : deux marketplaces peuvent réutiliser un id."""
        dedup = Deduplicator()
        assert dedup.add("mercari:m1") is True
        assert dedup.add("yahoo_auction:m1") is True

    def test_capacity_is_enforced(self):
        dedup = Deduplicator(capacity=10)
        for i in range(50):
            dedup.add(f"s:{i}")
        assert len(dedup) == 10
        # Les plus anciennes sont sorties : c'est le prix d'un cache borné,
        # et elles restent connues côté SQLite.
        assert dedup.seen("s:49")
        assert not dedup.seen("s:0")

    def test_priming_from_storage(self):
        dedup = Deduplicator()
        dedup.prime({"mercari:m1", "mercari:m2"})
        assert dedup.add("mercari:m1") is False

    def test_ratio(self):
        dedup = Deduplicator()
        dedup.add("a")
        dedup.add("a")
        assert dedup.duplicate_ratio == 0.5


class TestScheduler:
    def test_priorities_map_to_intervals(self):
        sched = Scheduler(budget_per_second=100.0)
        sched.sync([("s", "a", "high"), ("s", "b", "medium"), ("s", "c", "low")])
        intervals = {t.query: t.interval for t in sched.tasks.values()}
        assert intervals["a"] < intervals["b"] < intervals["c"]

    def test_budget_stretches_intervals(self):
        sched = Scheduler(budget_per_second=2.0)
        sched.sync([("s", f"q{i}", "medium") for i in range(20)])
        report = sched.report()
        assert report["saturated"] is True
        assert all(t.interval > t.target_interval for t in sched.tasks.values())

    def test_high_priority_keeps_the_larger_share(self):
        sched = Scheduler(budget_per_second=1.0)
        sched.sync([("s", "hot", "high")] + [("s", f"c{i}", "low") for i in range(9)])
        hot = sched.tasks["s::hot"]
        cold = sched.tasks["s::c0"]
        assert hot.interval < cold.interval

    def test_sync_preserves_existing_deadlines(self):
        """Replanifier ne doit pas provoquer une rafale de requêtes."""
        sched = Scheduler()
        sched.sync([("s", "a", "medium")])
        deadline = sched.tasks["s::a"].deadline
        sched.sync([("s", "a", "medium"), ("s", "b", "medium")])
        assert sched.tasks["s::a"].deadline == deadline

    def test_removed_tasks_disappear(self):
        sched = Scheduler()
        sched.sync([("s", "a", "medium"), ("s", "b", "medium")])
        added, removed = sched.sync([("s", "a", "medium")])
        assert removed == 1
        assert "s::b" not in sched.tasks

    def test_no_drift(self):
        """L'échéance suivante part de la précédente, pas de maintenant."""
        sched = Scheduler()
        sched.sync([("s", "a", "medium")])
        task = sched.tasks["s::a"]
        task.deadline = 100.0
        task.schedule_next(now=100.0, jitter=0.0)
        assert task.deadline == pytest.approx(105.0)

    def test_late_task_does_not_accumulate_debt(self):
        sched = Scheduler()
        sched.sync([("s", "a", "medium")])
        task = sched.tasks["s::a"]
        task.deadline = 0.0
        task.schedule_next(now=1000.0, jitter=0.0)
        assert task.deadline > 1000.0

    def test_due_orders_by_priority(self):
        sched = Scheduler(budget_per_second=100.0)
        sched.sync([("s", "low", "low"), ("s", "hot", "high")])
        for task in sched.tasks.values():
            task.deadline = 0.0
        assert sched.due(now=10.0)[0].priority == "high"


class TestMetrics:
    def test_percentiles(self):
        hist = Histogram()
        for value in range(1, 101):
            hist.add(value)
        assert hist.percentile(0.50) == pytest.approx(50, abs=1)
        assert hist.percentile(0.95) == pytest.approx(95, abs=1)

    def test_zero_is_not_recorded(self):
        """Zéro veut dire « inconnu » : l'enregistrer fausserait les p95."""
        hist = Histogram()
        hist.add(0)
        hist.add(100)
        assert hist.summary()["count"] == 1
        assert hist.percentile(0.50) == 100

    def test_empty_histogram_is_safe(self):
        assert Histogram().summary()["p95"] == 0

    def test_source_health_needs_three_errors(self):
        metrics = Metrics()
        stats = metrics.source("s")
        assert stats.healthy is None, "jamais testée ≠ en panne"
        stats.record_error("boom")
        assert stats.healthy is None
        stats.record_error("boom")
        stats.record_error("boom")
        assert stats.healthy is False

    def test_recovery_resets(self):
        metrics = Metrics()
        stats = metrics.source("s")
        for _ in range(3):
            stats.record_error("boom")
        stats.record_ok(120, 10)
        assert stats.healthy is True
        assert stats.consecutive_errors == 0

    def test_render_does_not_crash_when_empty(self):
        assert "SCAN STATUS" in Metrics().render()

    def test_unverified_source_is_flagged_in_the_report(self):
        metrics = Metrics()
        metrics.source("yahoo_auction", verified=False)
        assert "NON VÉRIFIÉE" in metrics.render()
