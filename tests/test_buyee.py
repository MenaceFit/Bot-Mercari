"""Liens Buyee et intégration dans la chaîne de détection."""

import time

import pytest

from mercari_sniper.backends.base import SearchPage, SearchQuery
from mercari_sniper.buyee import (
    DEFAULT_ITEM_TEMPLATE,
    buyee_url,
    is_consumer_item,
)
from mercari_sniper.config import Config, SourceConfig
from mercari_sniper.engine import SniperEngine
from mercari_sniper.events import EventBus
from mercari_sniper.models import Listing
from mercari_sniper.notifiers import DiscordNotifier
from mercari_sniper.store import Store


class TestUrlBuilding:
    def test_consumer_item_uses_item_route(self):
        assert buyee_url("m12345678901").endswith("/mercari/item/m12345678901")

    def test_shop_product_uses_shop_route(self):
        assert "/mercari/shops/" in buyee_url("aBcDeF123")

    def test_id_classification(self):
        assert is_consumer_item("m123")
        assert not is_consumer_item("abc123")
        assert not is_consumer_item("")

    def test_empty_id_yields_empty_url(self):
        # L'appelant masque le bouton plutôt que d'afficher un lien mort.
        assert buyee_url("") == ""
        assert buyee_url("   ") == ""

    def test_affiliate_id_is_appended(self):
        assert buyee_url("m1", affiliate_id="ABC").endswith("?aid=ABC")

    def test_affiliate_appended_with_ampersand_when_query_exists(self):
        url = buyee_url("m1", item_template="https://x/{id}?ref=1", affiliate_id="A")
        assert url == "https://x/m1?ref=1&aid=A"

    def test_template_is_overridable(self):
        """Le format n'a pas pu être vérifié en ligne : il doit rester réglable."""
        url = buyee_url("m1", item_template="https://autre.example/{id}")
        assert url == "https://autre.example/m1"

    def test_empty_template_disables_link(self):
        assert buyee_url("m1", item_template="") == ""

    def test_id_is_url_encoded(self):
        assert " " not in buyee_url("a b", shop_template="https://x/{id}")

    def test_default_template_targets_buyee(self):
        assert DEFAULT_ITEM_TEMPLATE.startswith("https://buyee.jp/")

    def test_extra_params(self):
        url = buyee_url("m1", extra_params={"lang": "fr"})
        assert url.endswith("?lang=fr")


class StubBackend:
    name = "stub"

    def __init__(self, items):
        self.items = items
        self.done = False

    async def search(self, query: SearchQuery) -> SearchPage:
        if self.done:
            return SearchPage(items=[])
        self.done = True
        return SearchPage(items=list(self.items))

    async def aclose(self):
        return None


def make_listing(item_id="m555", title="Nike Trail Jacket"):
    now = time.time()
    return Listing(
        id=item_id, title=title, price=8900,
        url=f"https://jp.mercari.com/item/{item_id}",
        created=int(now), detected_at=now,
    )


@pytest.fixture
async def context(tmp_path):
    config = Config()
    config.keywords = ["nike trail"]
    config.sources = [SourceConfig(query="nike")]
    config.storage.database = str(tmp_path / "t.db")
    config.path = tmp_path / "config.yaml"
    config.poll.warmup = False
    config.notify.console = False
    store = Store(config.storage.database)
    await store.open()
    yield config, store, EventBus()
    await store.close()


class TestEngineIntegration:
    async def test_listing_gets_a_buyee_link(self, context):
        config, store, bus = context
        engine = SniperEngine(config, StubBackend([make_listing()]), store, bus)
        await engine._poll_source(engine._sources["nike"])

        assert engine.feed[0]["buyee_url"].endswith("/mercari/item/m555")

    async def test_disabled_leaves_link_empty(self, context):
        config, store, bus = context
        config.buyee.enabled = False
        engine = SniperEngine(config, StubBackend([make_listing()]), store, bus)
        await engine._poll_source(engine._sources["nike"])

        assert engine.feed[0]["buyee_url"] == ""

    async def test_affiliate_id_flows_through(self, context):
        config, store, bus = context
        config.buyee.affiliate_id = "MON-ID"
        engine = SniperEngine(config, StubBackend([make_listing()]), store, bus)
        await engine._poll_source(engine._sources["nike"])

        assert "aid=MON-ID" in engine.feed[0]["buyee_url"]

    async def test_custom_template_flows_through(self, context):
        config, store, bus = context
        config.buyee.item_template = "https://proxy.example/{id}"
        engine = SniperEngine(config, StubBackend([make_listing()]), store, bus)
        await engine._poll_source(engine._sources["nike"])

        assert engine.feed[0]["buyee_url"] == "https://proxy.example/m555"

    async def test_link_is_persisted(self, context):
        config, store, bus = context
        engine = SniperEngine(config, StubBackend([make_listing()]), store, bus)
        await engine._poll_source(engine._sources["nike"])
        await store.flush()

        rows = await store.recent(1)
        assert rows[0]["buyee_url"].endswith("/mercari/item/m555")


class TestStoreMigration:
    async def test_existing_database_gains_the_column(self, tmp_path):
        """Une base créée avant la fonctionnalité doit s'ouvrir sans erreur."""
        import sqlite3

        path = tmp_path / "ancienne.db"
        legacy = sqlite3.connect(path)
        legacy.executescript(
            """CREATE TABLE listings (
                 id TEXT PRIMARY KEY, title TEXT NOT NULL, price INTEGER,
                 url TEXT NOT NULL, image TEXT, seller_id TEXT, source TEXT,
                 matched TEXT, rarity TEXT, created INTEGER,
                 detected_at REAL, latency_ms INTEGER, notified INTEGER);
               CREATE TABLE seen (id TEXT PRIMARY KEY, seen_at REAL NOT NULL);"""
        )
        legacy.execute(
            "INSERT INTO listings (id, title, url) VALUES ('m1', 'ancien', 'u')"
        )
        legacy.commit()
        legacy.close()

        store = Store(path)
        await store.open()
        try:
            rows = await store.recent(10)
            assert rows[0]["buyee_url"] == ""       # colonne ajoutée, vide
            assert rows[0]["title"] == "ancien"     # données préservées

            store.queue(make_listing("m2"))
            store.queue(make_listing("m2"))         # doublon ignoré
            await store.flush()
            assert len(await store.recent(10)) == 2
        finally:
            await store.close()


class TestDiscordEmbed:
    def test_order_field_present(self):
        notifier = DiscordNotifier("https://discord.com/api/webhooks/1/x")
        listing = make_listing()
        listing.matched = ["nike trail"]
        listing.rarity = "RARE"
        listing.buyee_url = "https://buyee.jp/item/mercari/item/m555"

        fields = {f["name"]: f for f in notifier._build_embed(listing)["fields"]}
        assert "📦 Commander" in fields
        assert listing.buyee_url in fields["📦 Commander"]["value"]
        # Pleine largeur : c'est l'action qu'on veut toucher du pouce.
        assert fields["📦 Commander"]["inline"] is False

    def test_field_absent_without_link(self):
        notifier = DiscordNotifier("https://discord.com/api/webhooks/1/x")
        listing = make_listing()
        listing.matched = ["nike trail"]
        listing.rarity = "RARE"

        names = [f["name"] for f in notifier._build_embed(listing)["fields"]]
        assert "📦 Commander" not in names
