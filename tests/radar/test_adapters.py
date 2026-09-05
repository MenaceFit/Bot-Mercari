"""Adapters : contrat, cartographie Buyee, extraction, calibration."""

import time

import pytest

from buyee_radar.adapters.base import (
    AdapterError,
    Listing,
    MarketplaceAdapter,
    SearchQuery,
    SupportLevel,
)
from buyee_radar.adapters.buyee_html import (
    MARKETPLACES,
    BuyeeAdapter,
    Selectors,
    _price,
    _timestamp,
)
from buyee_radar.adapters.calibrate import calibrate
from buyee_radar.adapters.simulator import PROFILES, SimulatorAdapter

from .helpers import ScriptedAdapter


class TestSupportLevels:
    """Le cahier des charges interdit de simuler une source. Chaque
    marketplace déclare donc ce qu'on sait VRAIMENT d'elle."""

    def test_every_marketplace_declares_a_note(self):
        for name, market in MARKETPLACES.items():
            assert market.support_note, f"{name} sans justification"

    def test_unsupported_sources_explain_why(self):
        for name, market in MARKETPLACES.items():
            if market.support is SupportLevel.UNSUPPORTED:
                assert len(market.support_note) > 60, (
                    f"{name} marquée non supportée sans explication utile"
                )

    def test_shopping_and_rakuten_are_unsupported(self):
        """Ce sont des catalogues de boutiques, pas des flux de nouveautés."""
        assert MARKETPLACES["jdi_shopping"].support is SupportLevel.UNSUPPORTED
        assert MARKETPLACES["rakuten"].support is SupportLevel.UNSUPPORTED

    def test_auction_namespace_is_url_verified(self):
        """C'est le seul dont la forme de recherche est directement attestée."""
        assert MARKETPLACES["jdi_auction"].support is SupportLevel.URL_VERIFIED

    def test_no_marketplace_claims_to_be_fully_verified(self):
        """Aucune page Buyee n'a pu être chargée pendant le développement."""
        assert all(
            m.support is not SupportLevel.VERIFIED for m in MARKETPLACES.values()
        )

    def test_uncalibrated_adapter_downgrades_itself(self):
        adapter = BuyeeAdapter(MARKETPLACES["mercari"])
        assert adapter.support is SupportLevel.NEEDS_SELECTORS
        assert "calibrate" in adapter.support_note

    def test_calibrated_adapter_reaches_its_declared_level(self):
        adapter = BuyeeAdapter(
            MARKETPLACES["jdi_auction"],
            selectors=Selectors(item="li.x", title="a"),
        )
        assert adapter.support is SupportLevel.URL_VERIFIED


class TestUrlConstruction:
    def _adapter(self, name="mercari"):
        return BuyeeAdapter(
            MARKETPLACES[name], selectors=Selectors(item="li", title="a")
        )

    def test_keyword_is_url_encoded(self):
        url = self._adapter().build_url(SearchQuery(text="ナイキ ディビジョン"))
        assert " " not in url
        assert "%E3%83%8A" in url

    def test_auction_uses_the_path_form(self):
        """Forme attestée : /item/search/query/{mot-clé}."""
        url = self._adapter("jdi_auction").build_url(SearchQuery(text="nike"))
        assert "/item/search/query/nike" in url

    def test_mercari_uses_its_namespace(self):
        url = self._adapter().build_url(SearchQuery(text="nike"))
        assert url.startswith("https://buyee.jp/mercari/search")

    def test_page_placeholder_is_filled(self):
        url = self._adapter().build_url(SearchQuery(text="x"), page=3)
        assert "page=3" in url

    def test_cursor_overrides_everything(self):
        url = self._adapter().build_url(
            SearchQuery(text="x", cursor="https://buyee.jp/next")
        )
        assert url == "https://buyee.jp/next"

    def test_price_bounds_are_passed(self):
        url = self._adapter().build_url(
            SearchQuery(text="x", min_price=3000, max_price=20000)
        )
        assert "price_min=3000" in url and "price_max=20000" in url


FIXTURE = """
<ul class="itemList">
  <li class="itemCard">
    <a class="itemCard__itemName" href="/mercari/item/m1234567890">ナイキ ディビジョン ジャケット</a>
    <span class="itemCard__itemPrice">8,900円</span>
    <img src="data:image/gif;base64,R0lGOD" data-src="https://img.example/1.jpg">
    <span class="itemCard__time">3分前</span>
  </li>
  <li class="itemCard">
    <a class="itemCard__itemName" href="/mercari/item/m9876543210">アンダーアーマー パーカー</a>
    <span class="itemCard__itemPrice">12,500円</span>
  </li>
  <li class="itemCard"><span>sans lien exploitable</span></li>
</ul>
<a class="pagination__next" rel="next" href="/mercari/search?page=2">次へ</a>
"""

SELECTORS = Selectors(
    item="li.itemCard", title=".itemCard__itemName", link="a.itemCard__itemName",
    price=".itemCard__itemPrice", image="img", time=".itemCard__time",
    next_page="a[rel=next]",
)


class TestExtraction:
    def _adapter(self):
        return BuyeeAdapter(MARKETPLACES["mercari"], selectors=SELECTORS)

    def test_listings_are_extracted(self):
        result = self._adapter().parse(FIXTURE, 1000.0, 1000.2)
        assert [item.listing_id for item in result] == ["m1234567890", "m9876543210"]
        assert result.listings[0].price == 8900

    def test_listing_without_identifier_is_skipped(self):
        """Sans id stable, l'annonce serait renotifiée à chaque scan."""
        assert len(self._adapter().parse(FIXTURE, 1000.0, 1000.2)) == 2

    def test_lazy_image_is_preferred_over_the_placeholder(self):
        result = self._adapter().parse(FIXTURE, 1000.0, 1000.2)
        assert result.listings[0].image_url == "https://img.example/1.jpg"

    def test_relative_urls_become_absolute(self):
        result = self._adapter().parse(FIXTURE, 1000.0, 1000.2)
        assert result.listings[0].url.startswith("https://buyee.jp/mercari/item/")

    def test_relative_time_is_parsed(self):
        """« 3分前 » donne une date, donc une latence calculable."""
        result = self._adapter().parse(FIXTURE, 1000.0, 1000.2)
        assert result.listings[0].created_at > 0

    def test_missing_time_stays_unknown(self):
        """La deuxième annonce n'a pas d'horodatage : pas de latence inventée."""
        result = self._adapter().parse(FIXTURE, 1000.0, 1000.2)
        assert result.listings[1].created_at == 0.0
        assert result.listings[1].latency_ms == 0

    def test_next_page_cursor(self):
        result = self._adapter().parse(FIXTURE, 1000.0, 1000.2)
        assert result.cursor.endswith("/mercari/search?page=2")

    def test_network_latency_is_recorded(self):
        assert self._adapter().parse(FIXTURE, 1000.0, 1000.25).latency_ms == 250

    def test_stale_selectors_warn_loudly(self, caplog):
        adapter = BuyeeAdapter(
            MARKETPLACES["mercari"], selectors=Selectors(item="div.gone", title="a")
        )
        with caplog.at_level("WARNING"):
            result = adapter.parse(FIXTURE, 1000.0, 1000.2)
        assert len(result) == 0
        assert "calibrate" in caplog.text.lower()

    async def test_unsupported_source_returns_not_ok(self):
        adapter = BuyeeAdapter(MARKETPLACES["rakuten"])
        result = await adapter.search(SearchQuery(text="x"))
        assert result.ok is False
        assert "non supportée" in result.error

    async def test_uncalibrated_source_returns_not_ok(self):
        adapter = BuyeeAdapter(MARKETPLACES["mercari"])
        result = await adapter.search(SearchQuery(text="x"))
        assert result.ok is False
        assert "calibr" in result.error


class TestPriceAndTime:
    @pytest.mark.parametrize("text,expected", [
        ("8,900円", 8900), ("¥12 500", 12500), ("1.200 yen", 1200),
        ("1,234,567円", 1234567), ("980円", 980), ("", 0), ("ナイキ", 0),
    ])
    def test_price(self, text, expected):
        assert _price(text) == expected

    @pytest.mark.parametrize("text", ["3分前", "2 hours ago", "45秒前", "1日前"])
    def test_relative_time_is_read(self, text):
        assert _timestamp(text) > 0

    @pytest.mark.parametrize("text", ["", "hier", "在庫あり"])
    def test_unreadable_time_stays_zero(self, text):
        """Zéro veut dire « inconnu ». Deviner fausserait les latences."""
        assert _timestamp(text) == 0.0


def _page(count=24, nav=8):
    """Page réaliste : menu répété (piège) + grille d'annonces."""
    menu = "".join(
        f'<li class="menuItem"><a href="/c/{i}">カテゴリ</a></li>' for i in range(nav)
    )
    cards = "".join(
        f'<li class="itemCard"><a class="itemCard__itemName" '
        f'href="/mercari/item/m{1000000 + i}">ナイキ 商品 {i}</a>'
        f'<span class="itemCard__itemPrice">{2800 + i * 100:,}円</span>'
        f'<img data-src="https://img.example/{i}.jpg">'
        f'<span class="itemCard__time">{i + 1}分前</span></li>'
        for i in range(count)
    )
    return (
        f'<html><body><nav class="globalNav"><ul>{menu}</ul></nav>'
        f'<div class="searchResults"><ul class="itemList">{cards}</ul></div>'
        f'<div class="pagination"><a class="pagination__next" rel="next" '
        f'href="/mercari/search?page=2">次へ</a></div></body></html>'
    )


class TestCalibration:
    """La calibration remplace des sélecteurs écrits à l'aveugle.

    C'est la réponse au fait que buyee.jp était inaccessible pendant le
    développement : plutôt que de deviner le balisage, on l'apprend sur une
    vraie page, sur la machine de l'utilisateur.
    """

    def test_finds_the_item_container(self):
        result = calibrate(_page(), source="mercari")
        assert result.ok
        assert result.selectors.item == "li.itemCard"

    def test_ignores_the_repeated_menu(self):
        """Un menu se répète aussi : sans garde-fou il serait choisi."""
        result = calibrate(_page(nav=30), source="mercari")
        assert "menu" not in result.selectors.item.lower()

    def test_extracts_a_verifiable_sample(self):
        """Proposer une calibration sans montrer ce qu'elle extrait serait
        aussi dangereux qu'un sélecteur écrit au hasard."""
        result = calibrate(_page(), source="mercari")
        assert len(result.sample) == 3
        assert all(item["title"] for item in result.sample)
        assert all(item["price"] > 0 for item in result.sample)

    def test_counts_the_items(self):
        assert calibrate(_page(count=17), source="x").items_found == 17

    def test_finds_the_pagination_link(self):
        assert calibrate(_page(), source="x").selectors.next_page

    def test_empty_page_fails_with_an_explanation(self):
        result = calibrate("<html><body><p>rien</p></body></html>", source="x")
        assert not result.ok
        assert result.error
        assert any("JavaScript" in note for note in result.notes)

    def test_page_without_prices_is_flagged(self):
        html = (
            "<ul>" + "".join(
                f'<li class="card"><a href="/item/{i}">titre {i}</a></li>'
                for i in range(10)
            ) + "</ul>"
        )
        result = calibrate(html, source="x")
        # Sans prix repérable, la structure n'est pas reconnue comme une
        # liste d'annonces — mieux vaut refuser que calibrer à moitié.
        assert not result.ok or any("prix" in note for note in result.notes)

    def test_render_is_readable(self):
        text = calibrate(_page(), source="mercari").render()
        assert "Sélecteurs découverts" in text
        assert "VÉRIFIE" in text


class TestSimulator:
    async def test_respects_the_query(self):
        adapter = SimulatorAdapter("sim_mercari", seed=1, noise_ratio=0.0)
        await adapter.search(SearchQuery(text="ナイキ", limit=30))
        result = await adapter.search(SearchQuery(text="ナイキ", limit=30))
        assert len(result) > 0
        assert all("ナイキ" in item.title for item in result)

    async def test_timestamps_make_latency_measurable(self):
        adapter = SimulatorAdapter("sim_mercari", seed=2)
        await adapter.search(SearchQuery(text="", limit=10))
        result = await adapter.search(SearchQuery(text="", limit=10))
        assert result.listings[0].latency_ms > 0

    async def test_newest_first(self):
        adapter = SimulatorAdapter("sim_rakuma", seed=3)
        await adapter.search(SearchQuery(text="", limit=40))
        result = await adapter.search(SearchQuery(text="", limit=40))
        dates = [item.created_at for item in result]
        assert dates == sorted(dates, reverse=True)

    async def test_error_rate(self):
        adapter = SimulatorAdapter("sim_mercari", seed=5, error_rate=1.0)
        with pytest.raises(AdapterError):
            await adapter.search(SearchQuery(text="x"))

    def test_every_profile_has_its_own_cadence(self):
        rates = {rate for rate, _ in PROFILES.values()}
        assert len(rates) > 1, "des profils identiques ne testeraient rien"


class TestProtocol:
    @pytest.mark.parametrize("adapter", [
        SimulatorAdapter("sim_mercari"),
        ScriptedAdapter(),
        BuyeeAdapter(MARKETPLACES["mercari"], selectors=Selectors(item="li", title="a")),
    ])
    def test_adapters_satisfy_the_protocol(self, adapter):
        assert isinstance(adapter, MarketplaceAdapter)


class TestListingModel:
    def test_latency_stages(self):
        t0 = 1000.0
        item = Listing(
            source="s", listing_id="1", title="t", url="u",
            created_at=t0, requested_at=t0 + 1.0, detected_at=t0 + 1.2,
            matched_at=t0 + 1.21, notified_at=t0 + 1.3,
        )
        assert item.network_ms == 200
        assert item.latency_ms == 1200
        assert item.pipeline_ms == 10
        assert item.notify_ms == 90
        assert item.end_to_end_ms == 1300

    def test_unknown_endpoint_is_zero_not_a_guess(self):
        item = Listing(source="s", listing_id="1", title="t", url="u",
                       created_at=0.0, detected_at=time.time())
        assert item.latency_ms == 0

    def test_key_is_source_scoped(self):
        assert Listing(source="mercari", listing_id="m1", title="t",
                       url="u").key == "mercari:m1"
