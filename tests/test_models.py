"""Parsing des annonces et calcul de latence."""

import time

from mercari_sniper.models import Listing, item_url


class TestItemUrl:
    def test_consumer_listing(self):
        assert item_url("m12345678901") == "https://jp.mercari.com/item/m12345678901"

    def test_shop_product_uses_other_route(self):
        assert item_url("aBcDeF123") == "https://jp.mercari.com/shops/product/aBcDeF123"


class TestFromApi:
    RAW = {
        "id": "m98765432101",
        "name": "ナイキ トレイル ジャケット",
        "price": "8900",
        "sellerId": "42",
        "status": "ITEM_STATUS_ON_SALE",
        "created": 1700000000,
        "updated": 1700000100,
        "thumbnails": ["https://static.mercdn.net/a.jpg", "https://b.jpg"],
        "categoryId": 5,
        "itemConditionId": 2,
    }

    def test_maps_core_fields(self):
        listing = Listing.from_api(self.RAW, source="ナイキ")
        assert listing.id == "m98765432101"
        assert listing.title == "ナイキ トレイル ジャケット"
        assert listing.price == 8900          # la chaîne est convertie
        assert listing.seller_id == "42"
        assert listing.source == "ナイキ"
        assert listing.url.endswith("/item/m98765432101")

    def test_takes_first_thumbnail(self):
        assert Listing.from_api(self.RAW).image == "https://static.mercdn.net/a.jpg"

    def test_sets_detection_time_to_now(self):
        before = time.time()
        listing = Listing.from_api(self.RAW)
        assert before <= listing.detected_at <= time.time()

    def test_missing_fields_do_not_raise(self):
        listing = Listing.from_api({"id": "m1"})
        assert listing.title == ""
        assert listing.price == 0
        assert listing.image == ""

    def test_garbage_price_becomes_zero(self):
        assert Listing.from_api({"id": "m1", "price": "gratuit"}).price == 0

    def test_empty_thumbnails(self):
        assert Listing.from_api({"id": "m1", "thumbnails": []}).image == ""


class TestLatency:
    def test_latency_is_detection_minus_creation(self):
        listing = Listing(
            id="m1", title="t", price=1, url="u",
            created=1000, detected_at=1003.5,
        )
        assert listing.latency_ms == 3500

    def test_zero_when_creation_unknown(self):
        listing = Listing(id="m1", title="t", price=1, url="u", detected_at=1003.5)
        assert listing.latency_ms == 0

    def test_never_negative_on_clock_skew(self):
        # L'horloge du serveur Mercari peut être légèrement en avance.
        listing = Listing(
            id="m1", title="t", price=1, url="u",
            created=2000, detected_at=1000,
        )
        assert listing.latency_ms == 0

    def test_to_dict_includes_latency(self):
        listing = Listing(
            id="m1", title="t", price=1, url="u", created=1000, detected_at=1002
        )
        data = listing.to_dict()
        assert data["latency_ms"] == 2000
        assert data["id"] == "m1"
