"""Buyee est la plateforme cible, les marketplaces sont des sources.

Ces tests verrouillent l'architecture demandée. Chacun correspond à une
règle de validation du cahier des charges : si l'un d'eux tombe, c'est que
le scanner a recommencé à traiter une marketplace comme la source
principale.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from buyee_radar.adapters.base import Listing, SearchResult, SupportLevel
from buyee_radar.buyee import (
    ADAPTERS,
    SOURCES,
    BuyeeSearchEngine,
    SourceRegistry,
    resolve,
    source_of_url,
)
from buyee_radar.buyee.adapters import (
    AmazonAdapter,
    CrossSearchAdapter,
    JDirectItemsAuctionAdapter,
    JDirectItemsFleamarketAdapter,
    MercariAdapter,
    RakumaAdapter,
    RakutenAdapter,
    ZozotownAdapter,
)


class TestRegistry:
    def test_every_source_has_a_named_adapter(self):
        """Règle n°8 : le code ne doit pas contenir qu'un adapter Mercari."""
        assert set(ADAPTERS) == set(SOURCES)
        assert len(ADAPTERS) >= 8
        for name, klass in ADAPTERS.items():
            assert klass.SOURCE_ID == name

    def test_named_adapters_are_distinct_classes(self):
        classes = {
            MercariAdapter, RakumaAdapter, JDirectItemsAuctionAdapter,
            JDirectItemsFleamarketAdapter, RakutenAdapter, AmazonAdapter,
            ZozotownAdapter, CrossSearchAdapter,
        }
        assert len(classes) == 8

    def test_mercari_is_not_privileged(self):
        """Mercari est une source parmi d'autres, pas la source."""
        mercari = SOURCES["mercari"]
        assert mercari.kind == SOURCES["rakuma"].kind
        assert mercari.id == "mercari"
        # Aucun code ne doit traiter Mercari comme un défaut implicite.
        from buyee_radar.buyee.registry import DISPLAY_ORDER
        assert DISPLAY_ORDER[0] == "crosssearch"

    def test_no_mercari_api_dependency_remains(self):
        """Mercapi → Mercari est acceptable ; Mercapi → Buyee, jamais."""
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[2] / "src" / "buyee_radar"
        assert not (root / "adapters" / "dpop.py").exists()
        for path in root.rglob("*.py"):
            text = path.read_text("utf-8").lower()
            assert "api.mercari.jp" not in text, path
            assert "mercapi" not in text, path

    def test_unsupported_sources_explain_why(self):
        for spec in SOURCES.values():
            if spec.support is SupportLevel.UNSUPPORTED:
                assert len(spec.support_note) > 60, spec.id
                assert not spec.searchable

    def test_no_source_claims_more_support_than_its_evidence(self):
        """URL_VERIFIED exige des URL réellement observées."""
        for spec in SOURCES.values():
            if spec.support is SupportLevel.URL_VERIFIED:
                assert spec.evidence, spec.id
            # Aucune source réelle ne peut se dire pleinement vérifiée :
            # aucune page Buyee n'a pu être chargée pendant l'écriture.
            assert spec.support is not SupportLevel.VERIFIED, spec.id

    def test_sources_without_a_known_url_are_not_invented(self):
        """LuxeWholeSale n'est connue que du cross-search : pas d'URL devinée."""
        assert SOURCES["luxewholesale"].search_url == ""
        assert not SOURCES["luxewholesale"].searchable

    def test_legacy_ids_still_resolve(self):
        assert resolve("jdi_auction") == "jdirectitems_auction"
        assert resolve("jdi_fleamarket") == "jdirectitems_fleamarket"
        assert resolve("mercari") == "mercari"

    def test_origin_link_refuses_to_invent(self):
        mercari = SOURCES["mercari"]
        assert mercari.origin_link("m123456789").endswith("/item/m123456789")
        # Un identifiant qui ne ressemble pas à celui de la marketplace ne
        # donne PAS de lien : mieux vaut pas de bouton qu'un bouton mort.
        assert mercari.origin_link("wat") == ""
        assert mercari.origin_link("") == ""

    def test_buy_link_is_a_buyee_link(self):
        for name in ("mercari", "rakuma", "jdirectitems_auction",
                     "jdirectitems_fleamarket"):
            link = SOURCES[name].buy_link("abc123")
            assert link.startswith("https://buyee.jp/"), name


class TestCrossSearchAttribution:
    """Une annonce du cross-search revient à SA marketplace, pas à « cross »."""

    @pytest.mark.parametrize("url,expected", [
        ("https://buyee.jp/mercari/item/m12345678901", "mercari"),
        ("https://buyee.jp/rakuma/item/372996ff8780f443c723133905af67d4", "rakuma"),
        ("https://buyee.jp/paypayfleamarket/item/z488929156",
         "jdirectitems_fleamarket"),
        ("https://buyee.jp/item/jdirectitems/auction/x1234567",
         "jdirectitems_auction"),
        ("https://example.com/whatever", ""),
    ])
    def test_source_of_url(self, url, expected):
        assert source_of_url(url) == expected

    def test_crosssearch_declares_what_buyee_declares(self):
        cross = SOURCES["crosssearch"]
        assert set(cross.aggregates) == {
            "mercari", "rakuma", "jdirectitems_auction",
            "jdirectitems_fleamarket", "luxewholesale",
        }
        for name in cross.aggregates:
            assert SOURCES[name].in_crosssearch, name


class ScriptedSource:
    """Une source scénarisée : sert à observer le moteur, pas le réseau."""

    def __init__(self, name, *, count=3, delay=0.05, fail=False):
        self.source = name
        self.label = name.title()
        self.support = SupportLevel.VERIFIED
        self.support_note = ""
        self.search_url = "https://example.invalid/{keyword}"
        self.calls = 0
        self._count, self._delay, self._fail = count, delay, fail

    async def start(self): ...
    async def stop(self): ...

    async def search(self, query):
        self.calls += 1
        started = time.time()
        await asyncio.sleep(self._delay)
        if self._fail:
            raise RuntimeError("la source est tombée")
        return SearchResult(
            source=self.source,
            listings=[
                Listing(source=self.source, listing_id=f"{self.source}-{i}",
                        title=f"{query.text} {i}", url="")
                for i in range(self._count)
            ],
            requested_at=started, received_at=time.time(),
        )


class TestSearchEngine:
    async def test_one_query_reaches_every_enabled_source(self):
        """Règle n°2 : le scanner ne doit pas n'interroger que Mercari."""
        registry = SourceRegistry()
        sources = [ScriptedSource(n) for n in
                   ("mercari", "rakuma", "jdirectitems_auction",
                    "jdirectitems_fleamarket")]
        for source in sources:
            registry.add(source)

        report = await BuyeeSearchEngine(registry).search("nike trail")

        assert all(s.calls == 1 for s in sources)
        assert len(report.queried) == 4
        assert len(report.listings) == 12

    async def test_sources_are_queried_in_parallel(self):
        """Règle : « pas Mercari d'abord puis Rakuma ». En parallèle."""
        registry = SourceRegistry()
        for name in ("mercari", "rakuma", "jdirectitems_auction",
                     "jdirectitems_fleamarket", "luxewholesale"):
            registry.add(ScriptedSource(name, delay=0.15))

        started = time.perf_counter()
        await BuyeeSearchEngine(registry).search("nike")
        elapsed = time.perf_counter() - started

        # Séquentiel : 5 × 150 ms = 750 ms. Parallèle : ~150 ms. La marge
        # est large pour rester stable sur une machine chargée.
        assert elapsed < 0.45, f"{elapsed:.3f}s — les sources partent en série"

    async def test_a_failing_source_does_not_stop_the_others(self):
        registry = SourceRegistry()
        registry.add(ScriptedSource("mercari"))
        registry.add(ScriptedSource("rakuma", fail=True))
        registry.add(ScriptedSource("jdirectitems_auction"))

        report = await BuyeeSearchEngine(registry).search("nike")

        assert len(report.listings) == 6
        failed = [o for o in report.outcomes if not o.ok and o.queried]
        assert len(failed) == 1 and failed[0].source == "rakuma"
        assert "tombée" in failed[0].error

    async def test_report_counts_match_what_was_queried(self):
        """Règle n°6 : le compte affiché doit être celui des sources réelles."""
        registry = SourceRegistry()
        registry.add(ScriptedSource("mercari"))
        registry.add(ScriptedSource("rakuma"), )
        registry.add(ScriptedSource("jdirectitems_auction"))
        registry.enable("jdirectitems_auction", False)

        report = await BuyeeSearchEngine(registry).search("nike")

        assert len(report.queried) == 2
        assert len(report.skipped) == 1
        assert report.skipped[0].skipped_reason
        assert sum(o.count for o in report.queried) == len(report.listings)

    async def test_unsupported_sources_are_never_queried(self):
        """Règle n°5 : une source non supportée n'est pas simulée."""
        registry = SourceRegistry.build(
            {name: {"enabled": True} for name in
             ("rakuten", "amazon", "zozotown", "jdirectitems_shopping")},
            include_unsupported=True,
        )
        report = await BuyeeSearchEngine(registry).search("nike")

        assert report.listings == []
        for outcome in report.outcomes:
            if outcome.source in ("rakuten", "amazon", "zozotown",
                                  "jdirectitems_shopping"):
                assert not outcome.queried
                assert "non supportée" in outcome.skipped_reason

    async def test_selected_sources_only(self):
        registry = SourceRegistry()
        for name in ("mercari", "rakuma", "jdirectitems_auction"):
            registry.add(ScriptedSource(name))

        report = await BuyeeSearchEngine(registry).search(
            "nike", sources=["rakuma"]
        )
        assert [o.source for o in report.queried] == ["rakuma"]

    async def test_per_source_breakdown(self):
        registry = SourceRegistry()
        registry.add(ScriptedSource("mercari", count=4))
        registry.add(ScriptedSource("rakuma", count=2))

        report = await BuyeeSearchEngine(registry).search("nike")
        assert report.per_source == {"mercari": 4, "rakuma": 2}


class TestRegistryCounts:
    def test_counts_never_overstate(self):
        registry = SourceRegistry.build(
            {"mercari": {"enabled": True}, "rakuten": {"enabled": True}},
            include_unsupported=True,
        )
        counts = registry.counts()
        assert counts["known" if "known" in counts else "total"] >= 8
        # Rakuten est non supportée : activée dans la config, elle ne compte
        # ni comme activée ni comme exploitable.
        assert counts["enabled"] == 1
        assert counts["usable"] == 0

    def test_enabling_an_unsupported_source_is_refused(self):
        registry = SourceRegistry.build({}, include_unsupported=True)
        assert registry.enable("zozotown", True) is False
        assert registry.enable("mercari", True) is True
