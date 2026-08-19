"""Ressources nécessaires à l'installation comme application mobile."""

import json

import pytest
from fastapi.testclient import TestClient

from mercari_sniper.backends.base import SearchPage, SearchQuery
from mercari_sniper.config import Config
from mercari_sniper.engine import SniperEngine
from mercari_sniper.events import EventBus
from mercari_sniper.server import WEB_DIR, create_app
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
    config.storage.database = str(tmp_path / "t.db")
    config.path = tmp_path / "config.yaml"
    config.notify.console = False
    store = Store(config.storage.database)
    await store.open()
    engine = SniperEngine(config, StubBackend(), store, EventBus())
    with TestClient(create_app(engine)) as api:
        yield api
    await store.close()


class TestManifest:
    def test_served_with_correct_type(self, client):
        response = client.get("/manifest.webmanifest")
        assert response.status_code == 200
        assert "manifest" in response.headers["content-type"]

    def test_declares_standalone_display(self, client):
        data = client.get("/manifest.webmanifest").json()
        # Sans `standalone`, Android ouvre un onglet au lieu d'une application.
        assert data["display"] == "standalone"
        assert data["start_url"] == "/"
        assert data["scope"] == "/"

    def test_has_the_icon_sizes_android_requires(self, client):
        icons = client.get("/manifest.webmanifest").json()["icons"]
        sizes = {icon["sizes"] for icon in icons}
        assert {"192x192", "512x512"} <= sizes

    def test_has_a_maskable_icon(self, client):
        icons = client.get("/manifest.webmanifest").json()["icons"]
        # Sans icône maskable, Android rogne l'icône n'importe comment.
        assert any("maskable" in icon.get("purpose", "") for icon in icons)

    def test_declared_icons_all_exist(self, client):
        for icon in client.get("/manifest.webmanifest").json()["icons"]:
            response = client.get(icon["src"])
            assert response.status_code == 200, icon["src"]
            assert response.headers["content-type"] == "image/png"
            assert response.content.startswith(b"\x89PNG")


class TestServiceWorker:
    def test_served_from_root(self, client):
        """Depuis /static/, il ne contrôlerait pas la page d'accueil."""
        response = client.get("/sw.js")
        assert response.status_code == 200
        assert "javascript" in response.headers["content-type"]

    def test_not_cached_by_the_browser(self, client):
        assert "no-cache" in client.get("/sw.js").headers.get("cache-control", "")

    def test_never_caches_live_data(self, client):
        source = client.get("/sw.js").text
        # Servir des annonces périmées depuis le cache serait pire qu'une erreur.
        assert "/api/" in source
        assert "url.pathname.startsWith('/api/')" in source

    def test_page_registers_it(self, client):
        assert "serviceWorker.register('/sw.js')" in client.get("/static/app.js").text


class TestMobileMeta:
    def test_page_links_the_manifest(self, client):
        assert 'rel="manifest"' in client.get("/").text

    def test_declares_theme_colour_for_both_schemes(self, client):
        html = client.get("/").text
        assert html.count('name="theme-color"') == 2

    def test_declares_apple_touch_icon(self, client):
        assert 'rel="apple-touch-icon"' in client.get("/").text

    def test_viewport_is_responsive(self, client):
        assert 'name="viewport"' in client.get("/").text


class TestAssetsOnDisk:
    @pytest.mark.parametrize(
        "name",
        ["manifest.webmanifest", "sw.js", "icon-192.png", "icon-512.png",
         "icon-maskable-512.png", "icon-180.png"],
    )
    def test_asset_is_packaged(self, name):
        assert (WEB_DIR / name).is_file(), f"{name} manquant"

    def test_manifest_is_valid_json(self):
        json.loads((WEB_DIR / "manifest.webmanifest").read_text("utf-8"))

    def test_mobile_styles_present(self):
        css = (WEB_DIR / "app.css").read_text("utf-8")
        assert "max-width: 640px" in css
        assert "env(safe-area-inset-bottom)" in css
