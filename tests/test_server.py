"""API HTTP et ressources du dashboard."""

import pytest
from fastapi.testclient import TestClient

from mercari_sniper.backends.base import SearchPage, SearchQuery
from mercari_sniper.config import Config, SourceConfig
from mercari_sniper.engine import SniperEngine
from mercari_sniper.events import EventBus
from mercari_sniper.server import create_app
from mercari_sniper.store import Store


class StubBackend:
    name = "stub"

    async def search(self, query: SearchQuery) -> SearchPage:
        return SearchPage(items=[])

    async def aclose(self):
        return None


@pytest.fixture
async def client(tmp_path):
    config = Config()
    config.storage.database = str(tmp_path / "test.db")
    config.path = tmp_path / "config.yaml"
    config.notify.console = False

    store = Store(config.storage.database)
    await store.open()
    engine = SniperEngine(config, StubBackend(), store, EventBus())
    with TestClient(create_app(engine)) as test_client:
        yield test_client, engine
    await store.close()


class TestPages:
    def test_dashboard_is_served(self, client):
        api, _ = client
        response = api.get("/")
        assert response.status_code == 200
        assert "Mercari" in response.text
        assert "/static/app.css" in response.text
        assert "/static/app.js" in response.text

    def test_stylesheet_is_served(self, client):
        api, _ = client
        response = api.get("/static/app.css")
        assert response.status_code == 200
        assert "--accent" in response.text

    def test_script_is_served(self, client):
        api, _ = client
        response = api.get("/static/app.js")
        assert response.status_code == 200
        assert "WebSocket" in response.text

    def test_favicon_is_served(self, client):
        api, _ = client
        response = api.get("/favicon.svg")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/svg")


class TestState:
    def test_health(self, client):
        api, _ = client
        assert api.get("/api/health").json()["ok"] is True

    def test_state_shape(self, client):
        api, _ = client
        data = api.get("/api/state").json()
        for key in ("stats", "sources", "keywords", "coverage", "activity",
                    "feed", "backend", "discord"):
            assert key in data, key

    def test_activity_has_thirty_buckets(self, client):
        api, _ = client
        assert len(api.get("/api/state").json()["activity"]) == 30

    def test_listings_endpoint(self, client):
        api, _ = client
        assert api.get("/api/listings?limit=5").json() == []


class TestKeywordEndpoints:
    def test_add_keyword_creates_source(self, client):
        api, engine = client
        response = api.post("/api/keywords", json={"keyword": "nike acg"})
        data = response.json()

        assert response.status_code == 200
        assert data["added"] is True
        assert data["source_created"] == "nike acg"
        assert [entry["keyword"] for entry in data["keywords"]] == ["nike acg"]
        assert "nike acg" in engine._sources

    def test_add_empty_keyword_is_rejected(self, client):
        api, _ = client
        response = api.post("/api/keywords", json={"keyword": "   "})
        assert response.status_code == 400

    def test_duplicate_keyword_is_reported(self, client):
        api, _ = client
        api.post("/api/keywords", json={"keyword": "nike acg"})
        data = api.post("/api/keywords", json={"keyword": "nike acg"}).json()
        assert data["added"] is False
        assert data["reason"]

    def test_remove_keyword(self, client):
        api, _ = client
        api.post("/api/keywords", json={"keyword": "nike acg"})
        data = api.delete("/api/keywords/nike%20acg").json()
        assert data["removed"] is True
        assert data["keywords"] == []

    def test_remove_unknown_keyword(self, client):
        api, _ = client
        assert api.delete("/api/keywords/inconnu").json()["removed"] is False

    def test_japanese_keyword_roundtrip(self, client):
        api, _ = client
        added = api.post("/api/keywords", json={"keyword": "ナイキ トレイル"}).json()
        assert added["added"] is True
        assert added["source_created"] == "ナイキ トレイル"

        removed = api.delete("/api/keywords/ナイキ トレイル").json()
        assert removed["removed"] is True

    def test_coverage_returned_with_each_change(self, client):
        api, _ = client
        data = api.post("/api/keywords", json={"keyword": "nike acg"}).json()
        assert data["coverage"]["uncovered_keywords"] == []


class TestSourceEndpoints:
    def test_add_source(self, client):
        api, engine = client
        data = api.post("/api/sources", json={"query": "ナイキ"}).json()
        assert data["added"] is True
        assert "ナイキ" in engine._sources

    def test_add_source_requires_query(self, client):
        api, _ = client
        assert api.post("/api/sources", json={"query": ""}).status_code == 400

    def test_pause_source(self, client):
        api, engine = client
        api.post("/api/sources", json={"query": "nike"})
        data = api.post("/api/sources/nike/pause", json={"paused": True}).json()
        assert data["ok"] is True
        assert engine._sources["nike"].paused is True

    def test_pause_unknown_source(self, client):
        api, _ = client
        assert api.post("/api/sources/absente/pause", json={}).json()["ok"] is False


class TestConfigEndpoint:
    def test_save_writes_the_file(self, client):
        api, engine = client
        api.post("/api/keywords", json={"keyword": "nike acg"})
        response = api.post("/api/config/save")

        assert response.status_code == 200
        assert engine.config.path.exists()
        assert "nike acg" in engine.config.path.read_text("utf-8")


class TestWebSocket:
    def test_snapshot_on_connect(self, client):
        api, _ = client
        with api.websocket_connect("/ws") as socket:
            message = socket.receive_json()
            assert message["type"] == "snapshot"
            assert "stats" in message["data"]
            assert "activity" in message["data"]

    def test_receives_published_events(self, client):
        api, engine = client
        with api.websocket_connect("/ws") as socket:
            socket.receive_json()          # instantané initial
            engine.bus.publish("stats", {"total_hits": 42})
            message = socket.receive_json()
            assert message["type"] == "stats"
            assert message["data"]["total_hits"] == 42


class TestFilterEndpoints:
    """Le filtre anti-bruit se pilote depuis le dashboard, sans éditer le YAML."""

    def test_filters_are_exposed(self, client):
        api, _ = client
        data = api.get("/api/filters").json()

        assert "beauty" in data["noise_groups"]
        assert {entry["name"] for entry in data["available"]} == {"beauty", "junk"}
        assert data["active_terms"] > 0
        assert all(entry["label"] for entry in data["available"])

    def test_snapshot_carries_the_filters(self, client):
        api, _ = client
        assert "filters" in api.get("/api/state").json()

    def test_group_can_be_disabled(self, client):
        api, engine = client
        data = api.post("/api/filters", json={"noise_groups": ["junk"]}).json()

        assert data["noise_groups"] == ["junk"]
        assert engine._is_excluded("dior 香水") is False
        assert engine._is_excluded("nike 空箱") is True

    def test_unknown_group_is_rejected_silently(self, client):
        """Une faute de frappe ne doit pas désactiver le filtre en silence."""
        api, _ = client
        data = api.post(
            "/api/filters", json={"noise_groups": ["beauty", "bogus"]}
        ).json()
        assert data["noise_groups"] == ["beauty"]

    def test_user_words_are_applied_immediately(self, client):
        api, engine = client
        api.post("/api/filters", json={"exclude_words": ["キッズ", "  "]})

        assert engine.config.filters.exclude_words == ["キッズ"]
        assert engine._is_excluded("ナイキ キッズ 22cm") is True

    def test_changes_reach_the_server_side_exclusion(self, client):
        api, engine = client
        api.post("/api/keywords", json={"keyword": "nike acg"})
        api.post("/api/filters", json={"exclude_words": ["キッズ"]})

        state = engine._sources["nike acg"]
        exclude = engine._query_for(state).exclude_keyword
        assert "キッズ" in exclude, "Mercari doit filtrer le bruit lui-même"

    def test_filters_are_persisted(self, client, tmp_path):
        api, engine = client
        api.post("/api/filters", json={"noise_groups": []})

        from mercari_sniper.config import Config
        reloaded = Config.load(engine.config.path)
        assert reloaded.filters.noise_groups == []
