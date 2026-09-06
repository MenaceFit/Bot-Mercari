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
from buyee_radar.platforms import (
    ADAPTERS,
    SOURCES,
    BuyeeSearchEngine,
    SourceRegistry,
    resolve,
    source_of_url,
)
from buyee_radar.platforms.adapters import (
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
        from buyee_radar.platforms.registry import DISPLAY_ORDER
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


class TestMandarakePlatform:
    """Seconde plateforme : une enseigne directe, hors Buyee."""

    def test_mandarake_is_its_own_platform(self):
        from buyee_radar.platforms.registry import PLATFORMS, platform_of

        assert "mandarake" in PLATFORMS
        assert platform_of("mandarake").id == "mandarake"
        assert platform_of("mercari").id == "buyee"
        assert SOURCES["mandarake"].platform == "mandarake"
        assert not SOURCES["mandarake"].in_crosssearch

    def test_named_adapter_exists(self):
        from buyee_radar.platforms.adapters import MandarakeAdapter

        assert ADAPTERS["mandarake"] is MandarakeAdapter
        assert MandarakeAdapter.SOURCE_ID == "mandarake"

    def test_search_url_is_the_attested_one(self):
        from buyee_radar.adapters.base import SearchQuery
        from buyee_radar.platforms.adapters import MandarakeAdapter

        url = MandarakeAdapter().build_url(SearchQuery(text="nike acg"), 1)
        assert url.startswith(
            "https://order.mandarake.co.jp/order/listPage/list?keyword="
        )
        assert "nike%20acg" in url
        assert "lang=en" in url

    def test_fresh_window_only_on_the_first_page(self):
        """`upToMinutes` sur le scan courant, jamais sur le rattrapage.

        Le rattrapage sert justement à récupérer ce qui est plus vieux que
        la fenêtre : l'y appliquer garantirait de ne jamais combler un trou.
        """
        from buyee_radar.adapters.base import SearchQuery
        from buyee_radar.platforms.adapters import MandarakeAdapter

        adapter = MandarakeAdapter()
        query = SearchQuery(text="nike")
        assert "upToMinutes=" in adapter.build_url(query, 1)
        assert "upToMinutes=" not in adapter.build_url(query, 2)
        assert "upToMinutes=" not in adapter.build_url(query, 5)

    def test_sorted_by_arrival(self):
        assert SOURCES["mandarake"].sort_newest.get("sort") == "arrival"

    def test_buy_link_is_mandarake_not_a_fake_proxy(self):
        """Mandarake expédie lui-même : aucun intermédiaire n'est inventé."""
        link = SOURCES["mandarake"].buy_link("1234567890")
        assert link.startswith("https://order.mandarake.co.jp/")
        assert "itemCode=1234567890" in link
        assert "buyee" not in link
        # Pas d'URL d'origine séparée : le lien d'achat EST la page d'origine.
        assert SOURCES["mandarake"].origin_link("1234567890") == ""

    def test_evidence_is_recorded(self):
        evidence = SOURCES["mandarake"].evidence
        assert len(evidence) >= 4
        assert any("listPage/list?keyword=" in url for url in evidence)


class TestListingIdFallback:
    """Un identifiant constant ferait passer toutes les annonces pour une.

    C'est la panne la plus dangereuse du système : la déduplication n'en
    garderait qu'une seule, et le bot se tairait sans jamais signaler
    d'erreur.
    """

    def test_id_in_the_query_string_is_extracted(self):
        import re

        pattern = SOURCES["mandarake"].id_from_url
        url = "https://order.mandarake.co.jp/order/detailPage/item?itemCode=1290452085&lang=en"
        assert re.search(pattern, url).group(1) == "1290452085"

    def test_generic_last_segment_never_becomes_an_id(self):
        from buyee_radar.adapters.buyee_html import _fallback_id

        first = _fallback_id("https://x.jp/order/detailPage/item?itemCode=1")
        second = _fallback_id("https://x.jp/order/detailPage/item?itemCode=2")
        assert first != second, "deux annonces distinctes ont le même identifiant"
        assert first not in ("item", "detailPage")

    def test_a_real_slug_is_kept_as_is(self):
        from buyee_radar.adapters.buyee_html import _fallback_id

        assert _fallback_id("https://buyee.jp/mercari/item/m123456") == "m123456"

    def test_fallback_is_stable_across_scans(self):
        from buyee_radar.adapters.buyee_html import _fallback_id

        url = "https://x.jp/list?itemCode=9"
        assert _fallback_id(url) == _fallback_id(url)


class TestTelegramOutput:
    """Le canal doit donner le nom, le prix en euros, et le lien."""

    def _listing(self, **kwargs):
        from buyee_radar.adapters.base import Listing

        listing = Listing(
            source=kwargs.pop("source", "jdirectitems_auction"),
            listing_id="x1",
            title=kwargs.pop("title", "NIKE ACG トレイル ジャケット"),
            url="https://buyee.jp/item/jdirectitems/auction/x1",
            buy_url=kwargs.pop("buy_url", "https://buyee.jp/item/jdirectitems/auction/x1"),
            price=kwargs.pop("price", 12500),
            **kwargs,
        )
        listing.price_eur = 76.25
        return listing

    def _notifier(self, **kwargs):
        from buyee_radar.notifications.telegram import TelegramNotifier

        return TelegramNotifier("token", "@canal", **kwargs)

    def test_clean_message_has_the_three_essentials(self):
        message = self._notifier().format(self._listing())
        assert "NIKE ACG トレイル ジャケット" in message
        assert "76 €" in message
        assert "https://buyee.jp/item/jdirectitems/auction/x1" in message

    def test_clean_message_stays_short(self):
        """Un canal se lit sur un téléphone : le lien doit rester visible."""
        message = self._notifier().format(self._listing(score=78, tier="VERY RARE"))
        assert len(message.splitlines()) <= 7

    def test_source_is_named_readably(self):
        message = self._notifier().format(self._listing())
        assert "JDirectItems Auction" in message
        assert "jdirectitems_auction" not in message

    def test_yen_alone_when_the_rate_is_unknown(self):
        """Un euro inventé serait pire qu'un yen seul."""
        from buyee_radar.adapters.base import Listing

        listing = Listing(source="mercari", listing_id="m1", title="T",
                          url="https://buyee.jp/mercari/item/m1", price=9800)
        message = self._notifier().format(listing)
        assert "€" not in message
        assert "9" in message and "800" in message

    def test_mandarake_link_says_the_right_platform(self):
        listing = self._listing(
            source="mandarake",
            buy_url="https://order.mandarake.co.jp/order/detailPage/item?itemCode=9",
        )
        message = self._notifier().format(listing)
        assert "Mandarake" in message
        assert "Ouvrir sur Buyee" not in message

    def test_titles_are_escaped(self):
        """Un titre est écrit par un vendeur inconnu."""
        message = self._notifier().format(
            self._listing(title='<script>alert(1)</script> & "x"')
        )
        assert "<script>" not in message
        assert "&lt;script&gt;" in message

    def test_channel_id_is_accepted(self):
        assert self._notifier().chat_id == "@canal"

    def test_detailed_style_adds_context_without_losing_the_link(self):
        message = self._notifier(style="detailed").format(
            self._listing(keyword="Nike ACG", score=78, tier="VERY RARE")
        )
        assert "Nike ACG" in message
        assert "76 €" in message
        assert "https://buyee.jp/item/jdirectitems/auction/x1" in message

    def test_unknown_style_falls_back_to_clean(self):
        assert self._notifier(style="wat").style == "clean"


class TestMetricsDoNotLeak:
    def test_reading_a_source_does_not_register_it(self):
        """Un affichage ne doit pas créer l'objet qu'il observe.

        L'API lit les compteurs des dix sources connues à chaque requête.
        Si la lecture les inscrivait, le bloc d'état console listerait des
        sources « jamais testées » que le planificateur n'a jamais vues.
        """
        from buyee_radar.core.metrics import Metrics

        metrics = Metrics()
        assert metrics.peek("zozotown") is None
        assert metrics.sources == {}

        metrics.source("mercari").requests.inc()
        assert set(metrics.sources) == {"mercari"}
        assert metrics.peek("mercari") is not None


class TestNotificationTargeting:
    """Viser un salon précis : sujet Telegram, fil Discord."""

    def _telegram(self, chat="-1001234567890", **kwargs):
        from buyee_radar.notifications.telegram import TelegramNotifier

        return TelegramNotifier("token", chat, **kwargs)

    def test_topic_is_sent_with_every_message(self):
        notifier = self._telegram(topic_id="42")
        assert notifier._target({})["message_thread_id"] == 42

    def test_no_topic_means_no_field(self):
        """Envoyer message_thread_id=null casserait un envoi normal."""
        assert self._telegram()._target({}) == {}
        assert self._telegram(topic_id="")._target({}) == {}
        assert self._telegram(topic_id=0)._target({}) == {}

    def test_an_unreadable_topic_does_not_stop_the_bot(self):
        """Le groupe reste joignable ; seul le rangement est perdu."""
        notifier = self._telegram(topic_id="pas-un-nombre")
        assert notifier.topic_id is None
        assert notifier._target({}) == {}

    def test_target_label_names_what_it_will_hit(self):
        assert "canal public" in self._telegram("@canal").target_label
        assert "chat privé" in self._telegram("123456").target_label
        assert "groupe" in self._telegram("-987").target_label
        assert "sujet #7" in self._telegram(topic_id=7).target_label

    def test_discord_thread_is_added_to_the_webhook(self):
        from buyee_radar.notifications.discord import DiscordNotifier

        url = "https://discord.com/api/webhooks/1/abc"
        assert DiscordNotifier(url, thread_id="99").webhook_url.endswith(
            "?thread_id=99"
        )
        # Sans fil, l'URL n'est pas touchée.
        assert DiscordNotifier(url).webhook_url == url
        # Déjà présent : pas de doublon.
        already = url + "?thread_id=99"
        assert DiscordNotifier(already, thread_id="99").webhook_url == already

    def test_discord_price_leads_with_euros(self):
        from buyee_radar.adapters.base import Listing
        from buyee_radar.notifications.discord import DiscordNotifier

        listing = Listing(source="mercari", listing_id="m1", title="T",
                          url="https://buyee.jp/mercari/item/m1", price=12500)
        listing.price_eur = 76.0
        embed = DiscordNotifier("https://discord.com/api/webhooks/1/x").build_embed(listing)
        price = next(f for f in embed["fields"] if f["name"] == "Prix")
        assert "76 €" in price["value"]

        listing.price_eur = 0.0
        embed = DiscordNotifier("https://discord.com/api/webhooks/1/x").build_embed(listing)
        price = next(f for f in embed["fields"] if f["name"] == "Prix")
        assert "€" not in price["value"]

    def test_targets_are_treated_as_secrets(self):
        """Un identifiant de salon n'a rien à faire dans un YAML partagé."""
        from buyee_radar.config.loader import SECRET_FIELDS

        assert "telegram_topic_id" in SECRET_FIELDS
        assert "discord_thread_id" in SECRET_FIELDS
