"""Sources : contrat commun, parsing, latences, source HTML configurable."""

import time

import pytest

from snipe.sources.base import (
    BaseSource,
    Listing,
    SearchQuery,
    SearchResult,
    SourceError,
    SourceHealth,
)
from snipe.sources.buyee import DEFAULT_TEMPLATES, buy_url
from snipe.sources.html_source import (
    HTMLSource,
    SelectorError,
    SelectorSpec,
    _price,
)
from snipe.sources.mercari import MercariSource
from snipe.sources.simulator import SimulatorSource

from .helpers import ScriptedSource


class TestListingLatency:
    """Une latence dont un bout est inconnu n'est pas une latence."""

    def test_stages_are_computed(self):
        t0 = 1000.0
        listing = Listing(
            id="m1", source="s", title="t", url="u",
            published_at=t0, requested_at=t0 + 1.0,
            received_at=t0 + 1.2, matched_at=t0 + 1.21, notified_at=t0 + 1.3,
        )
        assert listing.network_ms == 200
        assert listing.detection_ms == 1200
        assert listing.pipeline_ms == 10
        assert listing.notify_ms == 90
        # T0 → T5, pas T2 → T5 : la publication est le point de départ.
        assert listing.total_ms == 1300

    def test_missing_endpoint_yields_zero_not_a_guess(self):
        """Sans date de publication, la détection ne doit PAS être inventée."""
        listing = Listing(id="m1", source="s", title="t", url="u",
                          published_at=0.0, received_at=time.time())
        assert listing.detection_ms == 0

    def test_negative_interval_is_zero(self):
        listing = Listing(id="m1", source="s", title="t", url="u",
                          published_at=2000.0, received_at=1000.0)
        assert listing.detection_ms == 0

    def test_key_is_source_scoped(self):
        assert Listing(id="m1", source="mercari", title="t", url="u").key == "mercari:m1"


class TestProtocol:
    @pytest.mark.parametrize("source", [
        SimulatorSource(),
        ScriptedSource(),
    ])
    def test_sources_satisfy_the_protocol(self, source):
        assert isinstance(source, BaseSource)

    def test_mercari_satisfies_the_protocol(self):
        source = MercariSource()
        assert isinstance(source, BaseSource)
        assert source.name == "mercari"

    def test_html_source_satisfies_the_protocol(self):
        source = HTMLSource(
            name="x", search_url="https://x/?q={keyword}",
            selectors=SelectorSpec(item="li", title="a", item_id_from_link=r"/(\d+)"),
        )
        assert isinstance(source, BaseSource)


class TestSourceError:
    def test_rate_limit(self):
        error = SourceError("429", status=429, retry_after=12.0)
        assert error.is_rate_limit and error.retry_after == 12.0

    def test_blocked_is_not_a_rate_limit(self):
        assert SourceError("403", status=403).is_blocked
        assert not SourceError("403", status=403).is_rate_limit


class TestSimulator:
    async def test_returns_listings_matching_the_query(self):
        source = SimulatorSource(seed=1, new_per_second=200.0, noise_ratio=0.0)
        await source.search(SearchQuery(text="ナイキ", limit=30))
        result = await source.search(SearchQuery(text="ナイキ", limit=30))
        assert len(result) > 0
        assert all("ナイキ" in item.title for item in result)
        await source.close()

    async def test_timestamps_are_populated(self):
        source = SimulatorSource(seed=2, new_per_second=200.0)
        await source.search(SearchQuery(text="", limit=10))
        result = await source.search(SearchQuery(text="", limit=10))
        item = result.listings[0]
        assert item.published_at > 0 and item.received_at > 0
        assert item.detection_ms > 0
        await source.close()

    async def test_results_are_newest_first(self):
        source = SimulatorSource(seed=3, new_per_second=300.0)
        await source.search(SearchQuery(text="", limit=40))
        result = await source.search(SearchQuery(text="", limit=40))
        dates = [item.published_at for item in result]
        assert dates == sorted(dates, reverse=True)
        await source.close()

    async def test_closed_source_raises(self):
        source = SimulatorSource()
        await source.close()
        with pytest.raises(SourceError):
            await source.search(SearchQuery(text="x"))

    async def test_error_rate_is_honoured(self):
        source = SimulatorSource(seed=5, error_rate=1.0)
        with pytest.raises(SourceError):
            await source.search(SearchQuery(text="x"))
        await source.close()


FIXTURE_HTML = """
<ul>
  <li class="Product">
    <a class="Product__titleLink" href="/auction/x1234567">ナイキ ディビジョン ジャケット</a>
    <span class="Product__priceValue">8,900円</span>
    <img src="data:image/gif;base64,R0lGOD" data-src="https://img.example/1.jpg">
    <div class="Product__seller"><a>vendeur_a</a></div>
  </li>
  <li class="Product">
    <a class="Product__titleLink" href="/auction/y7654321">アンダーアーマー パーカー</a>
    <span class="Product__priceValue">12,500円</span>
  </li>
  <li class="Product"><span>sans lien exploitable</span></li>
</ul>
<a class="Pager__link--next" href="/search?p=x&amp;b=51">suivant</a>
"""

SELECTORS = SelectorSpec(
    item="li.Product",
    title=".Product__titleLink",
    link="a.Product__titleLink",
    price=".Product__priceValue",
    image="img",
    seller=".Product__seller a",
    item_id_from_link=r"/auction/([a-zA-Z0-9]+)",
    next_page="a.Pager__link--next",
)


class TestHTMLSource:
    def _source(self, **kwargs):
        return HTMLSource(
            name="fixture",
            search_url="https://example.invalid/search?p={keyword}",
            selectors=kwargs.pop("selectors", SELECTORS),
            base_url="https://example.invalid/",
            **kwargs,
        )

    def test_extracts_listings(self):
        result = self._source().parse(FIXTURE_HTML, 1000.0, 1000.2)
        assert [item.id for item in result] == ["x1234567", "y7654321"]
        assert result.listings[0].price == 8900
        assert result.listings[0].seller == "vendeur_a"

    def test_listing_without_id_is_skipped(self):
        """Sans identifiant stable, l'annonce serait renotifiée à chaque scan."""
        result = self._source().parse(FIXTURE_HTML, 1000.0, 1000.2)
        assert len(result) == 2

    def test_lazy_loaded_image_is_preferred(self):
        """`src` contient un GIF transparent, la vraie image est en data-src."""
        result = self._source().parse(FIXTURE_HTML, 1000.0, 1000.2)
        assert result.listings[0].image_url == "https://img.example/1.jpg"

    def test_relative_urls_are_absolute(self):
        result = self._source().parse(FIXTURE_HTML, 1000.0, 1000.2)
        assert result.listings[0].url.startswith("https://example.invalid/auction/")

    def test_next_page_cursor(self):
        result = self._source().parse(FIXTURE_HTML, 1000.0, 1000.2)
        assert result.cursor.endswith("/search?p=x&b=51")

    def test_published_at_stays_unknown(self):
        """Une page de liste ne donne pas l'heure de publication."""
        result = self._source().parse(FIXTURE_HTML, 1000.0, 1000.2)
        assert all(item.published_at == 0.0 for item in result)
        assert all(item.detection_ms == 0 for item in result)

    def test_network_latency_is_recorded(self):
        result = self._source().parse(FIXTURE_HTML, 1000.0, 1000.25)
        assert result.latency_ms == 250

    def test_stale_selectors_produce_nothing_and_warn(self, caplog):
        source = self._source(
            selectors=SelectorSpec(item="div.gone", title="a",
                                   item_id_from_link=r"/(\d+)")
        )
        with caplog.at_level("WARNING"):
            result = source.parse(FIXTURE_HTML, 1000.0, 1000.2)
        assert len(result) == 0
        assert "sélecteurs" in caplog.text.lower()

    def test_url_is_built_and_encoded(self):
        url = self._source().build_url(SearchQuery(text="ナイキ ディビジョン"))
        assert url.startswith("https://example.invalid/search?p=")
        assert " " not in url

    def test_cursor_overrides_the_url(self):
        url = self._source().build_url(
            SearchQuery(text="x", cursor="https://example.invalid/page2")
        )
        assert url == "https://example.invalid/page2"


class TestSelectorValidation:
    """Une configuration incomplète est refusée AU DÉMARRAGE.

    Une source mal configurée ne lève aucune erreur à l'exécution : elle
    renvoie zéro annonce, en silence. C'est le pire mode de panne.
    """

    def test_missing_item_selector(self):
        with pytest.raises(SelectorError):
            SelectorSpec(item="", title="a").validate()

    def test_missing_title_selector(self):
        with pytest.raises(SelectorError):
            SelectorSpec(item="li", title="").validate()

    def test_missing_identifier(self):
        with pytest.raises(SelectorError, match="déduplication"):
            SelectorSpec(item="li", title="a").validate()

    def test_search_url_must_contain_the_placeholder(self):
        with pytest.raises(SelectorError, match="keyword"):
            HTMLSource(
                name="x", search_url="https://x/search",
                selectors=SelectorSpec(item="li", title="a", item_id_attr="data-id"),
            )


class TestPriceParsing:
    @pytest.mark.parametrize("text,expected", [
        ("8,900円", 8900),
        ("¥12 500", 12500),
        ("1.200 yen", 1200),          # notation européenne des milliers
        ("1,234,567円", 1234567),
        ("980円", 980),
        ("", 0),
        ("ナイキ", 0),
        ("0円", 0),
    ])
    def test_parses(self, text, expected):
        assert _price(text) == expected


class TestBuyeeUrls:
    def test_known_marketplace(self):
        url = buy_url("mercari", "m123456")
        assert url == "https://buyee.jp/item/mercari/item/m123456"

    def test_unknown_marketplace_returns_nothing(self):
        """Mieux vaut pas de bouton qu'un bouton vers une page d'erreur."""
        assert buy_url("inconnue", "x1") == ""

    def test_empty_id(self):
        assert buy_url("mercari", "") == ""

    def test_affiliate_id_is_appended(self):
        url = buy_url("mercari", "m1", affiliate_id="abc")
        assert url.endswith("?aid=abc")

    def test_custom_template_overrides(self):
        url = buy_url("mercari", "m1", templates={"mercari": "https://x/{id}"})
        assert url == "https://x/m1"

    def test_id_is_url_encoded(self):
        assert "%2F" in buy_url("mercari", "a/b")

    def test_broken_template_is_survivable(self):
        assert buy_url("mercari", "m1", templates={"mercari": "https://x/{oops}"}) == ""

    def test_every_default_template_has_the_placeholder(self):
        assert all("{id}" in tpl for tpl in DEFAULT_TEMPLATES.values())


class TestMercariRequestBody:
    """Le corps de requête sans toucher au réseau — l'API est inaccessible ici."""

    def test_sorted_by_creation_descending(self):
        body = MercariSource()._body(SearchQuery(text="ナイキ"))
        condition = body["searchCondition"]
        assert condition["sort"] == "SORT_CREATED_TIME"
        assert condition["order"] == "ORDER_DESC"

    def test_only_on_sale_items(self):
        body = MercariSource()._body(SearchQuery(text="x"))
        assert body["searchCondition"]["status"] == ["STATUS_ON_SALE"]

    def test_page_size_is_capped_at_the_api_limit(self):
        body = MercariSource()._body(SearchQuery(text="x", limit=500))
        assert body["pageSize"] == 120

    def test_exclusion_reaches_the_server(self):
        body = MercariSource()._body(SearchQuery(text="x", exclude_text="香水 コスメ"))
        assert body["searchCondition"]["excludeKeyword"] == "香水 コスメ"

    def test_price_bounds(self):
        body = MercariSource()._body(
            SearchQuery(text="x", min_price=3000, max_price=20000)
        )
        assert body["searchCondition"]["priceMin"] == 3000
        assert body["searchCondition"]["priceMax"] == 20000

    def test_cursor_becomes_page_token(self):
        body = MercariSource()._body(SearchQuery(text="x", cursor="tok"))
        assert body["pageToken"] == "tok"

    def test_marked_unverified(self):
        """L'API n'a pas pu être exercée : la source doit le dire."""
        assert MercariSource().verified is False


class TestMercariParsing:
    """Parsing sur une réponse capturée, sans réseau."""

    PAYLOAD = {
        "items": [
            {
                "id": "m12345678901",
                "name": "ナイキ ディビジョン ジャケット",
                "price": "8900",
                "created": "1700000000",
                "updated": "1700000000",
                "sellerId": "s1",
                "thumbnails": ["https://img.example/1.jpg"],
                "itemConditionId": "2",
                "categoryId": "1",
            },
            {"id": "", "name": "sans identifiant"},
            {"not_a_dict": True},
        ],
        "nextPageToken": "tok2",
    }

    def _parse(self):
        return MercariSource()._parse(self.PAYLOAD, 1000.0, 1000.15)

    def test_valid_items_only(self):
        assert [item.id for item in self._parse()] == ["m12345678901"]

    def test_fields_are_mapped(self):
        item = self._parse().listings[0]
        assert item.price == 8900
        assert item.published_at == 1700000000.0
        assert item.image_url == "https://img.example/1.jpg"
        assert item.source == "mercari"

    def test_item_url_shape(self):
        item = self._parse().listings[0]
        assert item.url == "https://jp.mercari.com/item/m12345678901"

    def test_shop_products_use_the_other_route(self):
        source = MercariSource()
        result = source._parse(
            {"items": [{"id": "aBcD1234", "name": "x", "price": 1}]}, 1.0, 2.0
        )
        assert "/shops/product/" in result.listings[0].url

    def test_cursor(self):
        assert self._parse().cursor == "tok2"

    def test_latency_is_carried(self):
        assert self._parse().latency_ms == 150

    def test_malformed_item_does_not_break_the_page(self):
        """Perdre une annonce vaut mieux que perdre la page entière."""
        result = MercariSource()._parse(
            {"items": [{"id": "m1", "name": None, "price": {"bad": 1}}]}, 1.0, 2.0
        )
        assert isinstance(result, SearchResult)
