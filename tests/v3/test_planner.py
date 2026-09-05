"""Planificateur de requêtes : l'arbitrage entre économie et exhaustivité.

C'est le module qui décide combien d'appels réseau partent, donc celui qui
décide de la latence ET du taux de découverte. Ses deux modes doivent être
vérifiés séparément, ainsi que le passage de l'un à l'autre.
"""

import pytest

from snipe.core.keywords import Keyword
from snipe.core.planner import MIN_SAMPLE, PlannedQuery, QueryPlanner


@pytest.fixture
def nike_family():
    return [
        Keyword.build("Nike Division", search=["ナイキ"],
                      include=["ディビジョン", "division"], priority="high"),
        Keyword.build("Nike Trail", search=["ナイキ"], include=["トレイル", "trail"]),
        Keyword.build("Nike Tokyo", search=["ナイキ"], include=["東京", "tokyo"]),
        Keyword.build("Under Armour", search=["アンダーアーマー"],
                      include=["アンダーアーマー"], priority="low"),
    ]


class TestGrouping:
    """« FEW REMOTE REQUESTS » : le mode économe, celui par défaut."""

    def test_shared_search_costs_one_request(self, nike_family):
        plan = QueryPlanner().plan(nike_family)
        assert len(plan) == 2, "3 mots-clés ナイキ + 1 UA = 2 requêtes"

    def test_every_keyword_stays_covered(self, nike_family):
        plan = QueryPlanner().plan(nike_family)
        covered = {name for query in plan for name in query.keywords}
        assert covered == {kw.display_name for kw in nike_family}

    def test_broader_query_absorbs_the_narrower_one(self):
        keywords = [
            Keyword.build("A", search=["ナイキ"], include=["a"]),
            Keyword.build("B", search=["ナイキ ディビジョン"], include=["b"]),
        ]
        plan = QueryPlanner().plan(keywords)
        assert [q.text for q in plan] == ["ナイキ"]
        assert set(plan[0].keywords) == {"A", "B"}

    def test_query_inherits_the_highest_priority_it_serves(self, nike_family):
        """Regrouper un mot-clé urgent ne doit pas le ralentir."""
        plan = {q.text: q for q in QueryPlanner().plan(nike_family)}
        assert plan["ナイキ"].priority == "high"
        assert plan["アンダーアーマー"].priority == "low"

    def test_disabled_keyword_is_not_planned(self, nike_family):
        nike_family[0].enabled = False
        plan = QueryPlanner().plan(nike_family)
        covered = {name for query in plan for name in query.keywords}
        assert "Nike Division" not in covered

    def test_source_scoped_keyword(self):
        keywords = [
            Keyword.build("M", search=["a"], sources=["mercari"]),
            Keyword.build("Y", search=["b"], sources=["yahoo_auction"]),
        ]
        plan = QueryPlanner().plan(keywords, source="mercari")
        assert [q.text for q in plan] == ["a"]

    def test_no_keywords_no_queries(self):
        assert QueryPlanner().plan([]) == []


class TestDemotionOnGap:
    """Un trou prouve que la requête large perd des annonces MAINTENANT."""

    def test_gap_splits_into_precise_queries(self, nike_family):
        planner = QueryPlanner()
        assert len(planner.plan(nike_family)) == 2

        assert planner.record_gap("ナイキ") is True
        plan = planner.plan(nike_family)

        assert len(plan) > 2, "la requête large doit avoir été remplacée"
        assert "ナイキ" not in [q.text for q in plan]

    def test_precise_queries_combine_search_and_include(self, nike_family):
        """« ナイキ » + « ディビジョン » → « ナイキ ディビジョン »."""
        planner = QueryPlanner()
        planner.record_gap("ナイキ")
        texts = {q.text for q in planner.plan(nike_family)}
        assert "ナイキ ディビジョン" in texts
        assert "ナイキ division" in texts

    def test_both_writings_get_their_own_query(self, nike_family):
        """« ナイキ ディビジョン » ne trouverait pas « NIKE DIVISION »."""
        planner = QueryPlanner()
        planner.record_gap("ナイキ")
        division = [
            q for q in planner.plan(nike_family) if "Nike Division" in q.keywords
        ]
        assert len(division) == 2

    def test_coverage_survives_demotion(self, nike_family):
        planner = QueryPlanner()
        planner.record_gap("ナイキ")
        plan = planner.plan(nike_family)
        covered = {name for query in plan for name in query.keywords}
        assert covered == {kw.display_name for kw in nike_family}

    def test_untouched_queries_stay_grouped(self, nike_family):
        planner = QueryPlanner()
        planner.record_gap("ナイキ")
        plan = {q.text: q for q in planner.plan(nike_family)}
        assert "アンダーアーマー" in plan

    def test_demotion_is_permanent(self, nike_family):
        """Repasser en mode large recommencerait à perdre des annonces."""
        planner = QueryPlanner()
        planner.record_gap("ナイキ")
        for _ in range(5):
            assert "ナイキ" not in [q.text for q in planner.plan(nike_family)]

    def test_second_gap_changes_nothing(self, nike_family):
        planner = QueryPlanner()
        assert planner.record_gap("ナイキ") is True
        assert planner.record_gap("ナイキ") is False

    def test_keyword_without_include_falls_back_to_its_name(self):
        """Rien de plus précis à proposer : on interroge le nom lui-même."""
        keywords = [Keyword.build("Arcteryx Veilance", search=["arcteryx"])]
        planner = QueryPlanner()
        planner.record_gap("arcteryx")
        assert [q.text for q in planner.plan(keywords)] == ["Arcteryx Veilance"]

    def test_synthesised_queries_are_capped(self):
        """Huit écritures alternatives ne doivent pas coûter huit requêtes."""
        keyword = Keyword.build(
            "Multi", search=["x"], include=[f"v{i}" for i in range(8)]
        )
        planner = QueryPlanner(max_forms=3)
        planner.record_gap("x")
        assert len(planner.plan([keyword])) == 3


class TestDemotionOnYield:
    """Signal faible : la requête ne perd rien, mais gaspille le budget."""

    def _planner_with(self, seen, hits):
        planner = QueryPlanner()
        planner.record_page("ナイキ", seen, hits)
        return planner

    def test_low_yield_is_demoted(self, nike_family):
        planner = self._planner_with(MIN_SAMPLE + 100, 1)
        assert planner.review_yield() == ["ナイキ"]

    def test_evidence_is_required(self, nike_family):
        """Sur 10 annonces, un rendement nul ne veut rien dire."""
        planner = self._planner_with(10, 0)
        assert planner.review_yield() == []

    def test_productive_query_is_left_alone(self, nike_family):
        planner = self._planner_with(MIN_SAMPLE + 100, 200)
        assert planner.review_yield() == []

    def test_yield_is_computed(self):
        planner = QueryPlanner()
        planner.record_page("q", 1000, 25)
        assert planner.yield_of("q") == pytest.approx(0.025)

    def test_report_shape(self):
        planner = QueryPlanner()
        planner.record_page("q", 100, 3)
        planner.record_gap("q")
        report = planner.report()
        assert report["queries"]["q"]["items_seen"] == 100
        assert report["queries"]["q"]["gaps"] == 1
        assert "q" in report["demoted"]


class TestPlannedQuery:
    def test_normalization(self):
        assert PlannedQuery(text="ＮＩＫＥ", keywords=()).normalized == "nike"
