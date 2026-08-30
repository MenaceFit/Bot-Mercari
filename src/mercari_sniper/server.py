"""API REST + WebSocket alimentant le dashboard local."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import noise
from .engine import SniperEngine

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent / "web"


def _serve(path: Path, media_type: str, headers: dict | None = None):
    """Sert un fichier de la coquille web, ou un 404 lisible s'il manque."""
    if not path.exists():
        return JSONResponse({"error": f"{path.name} absent"}, status_code=404)
    return FileResponse(path, media_type=media_type, headers=headers)


def _filters_payload(engine: SniperEngine) -> dict[str, Any]:
    filters = engine.config.filters
    return {
        "noise_groups": list(filters.noise_groups),
        "exclude_words": list(filters.exclude_words),
        "available": [
            {
                "name": name,
                "label": noise.GROUP_LABELS.get(name, name),
                "terms": len(terms),
                "sample": list(terms[:6]),
            }
            for name, terms in noise.GROUPS.items()
        ],
        "active_terms": len(filters.all_exclude_terms()),
    }


async def build_snapshot(engine: SniperEngine) -> dict[str, Any]:
    """État complet envoyé à l'ouverture du dashboard."""
    store_stats = await engine.store.stats()
    return {
        "stats": {**engine.stats(), **store_stats},
        "sources": engine.sources_state(),
        "keywords": engine.keyword_stats(),
        "coverage": engine.coverage(),
        "activity": engine.activity(),
        "feed": list(engine.feed),
        "backend": engine.backend.name,
        "discord": bool(engine.config.discord_webhook),
        "filters": _filters_payload(engine),
    }


def create_app(engine: SniperEngine) -> FastAPI:
    app = FastAPI(title="Mercari Sniper", docs_url=None, redoc_url=None)

    if WEB_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    # ── Dashboard ─────────────────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    async def dashboard() -> HTMLResponse:
        index = WEB_DIR / "index.html"
        if not index.exists():
            return HTMLResponse("<h1>Dashboard introuvable</h1>", status_code=500)
        return HTMLResponse(index.read_text("utf-8"))

    # response_model=None : le type de retour varie, FastAPI ne doit pas
    # tenter d'en dériver un modèle Pydantic.
    @app.get("/favicon.svg", response_model=None)
    async def favicon():
        return _serve(WEB_DIR / "favicon.svg", "image/svg+xml")

    @app.get("/manifest.webmanifest", response_model=None)
    async def manifest():
        return _serve(WEB_DIR / "manifest.webmanifest", "application/manifest+json")

    # Le service worker doit être servi depuis la racine : son périmètre de
    # contrôle ne peut pas remonter au-dessus de son propre chemin. Servi
    # depuis /static/, il ne contrôlerait pas la page d'accueil.
    @app.get("/sw.js", response_model=None)
    async def service_worker():
        return _serve(
            WEB_DIR / "sw.js",
            "application/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    # ── État ──────────────────────────────────────────────────────────────
    @app.get("/api/state")
    async def state() -> JSONResponse:
        return JSONResponse(await build_snapshot(engine))

    @app.get("/api/listings")
    async def listings(limit: int = 100, keyword: str | None = None) -> JSONResponse:
        limit = max(1, min(1000, limit))
        return JSONResponse(await engine.store.recent(limit, keyword))

    @app.get("/api/health")
    async def health() -> JSONResponse:
        return JSONResponse({"ok": True, "running": engine.running})

    # ── Keywords ──────────────────────────────────────────────────────────
    @app.post("/api/keywords")
    async def add_keyword(payload: dict) -> JSONResponse:
        keyword = str(payload.get("keyword", "")).strip()
        if not keyword:
            return JSONResponse({"error": "keyword requis"}, status_code=400)

        result = await engine.add_keyword(keyword)
        engine.bus.publish("keywords", engine.keyword_stats())
        return JSONResponse(
            {
                **result,
                "keywords": engine.keyword_stats(),
                "sources": engine.sources_state(),
                "coverage": engine.coverage(),
            }
        )

    @app.delete("/api/keywords/{keyword:path}")
    async def remove_keyword(keyword: str) -> JSONResponse:
        removed = await engine.remove_keyword(keyword)
        engine.bus.publish("keywords", engine.keyword_stats())
        return JSONResponse(
            {
                "removed": removed,
                "keywords": engine.keyword_stats(),
                "sources": engine.sources_state(),
                "coverage": engine.coverage(),
            }
        )

    # ── Sources ───────────────────────────────────────────────────────────
    @app.post("/api/sources")
    async def add_source(payload: dict) -> JSONResponse:
        query = str(payload.get("query", "")).strip()
        if not query:
            return JSONResponse({"error": "query requise"}, status_code=400)
        added = await engine.add_source(query, page_size=120)
        return JSONResponse({"added": added, "sources": engine.sources_state()})

    @app.post("/api/sources/{query:path}/pause")
    async def pause_source(query: str, payload: dict | None = None) -> JSONResponse:
        paused = bool((payload or {}).get("paused", True))
        ok = engine.set_source_paused(query, paused)
        return JSONResponse(
            {"ok": ok, "paused": paused, "sources": engine.sources_state()}
        )

    # ── Filtres de bruit ──────────────────────────────────────────────────
    @app.get("/api/filters")
    async def get_filters() -> JSONResponse:
        return JSONResponse(_filters_payload(engine))

    @app.post("/api/filters")
    async def set_filters(payload: dict) -> JSONResponse:
        filters = engine.config.filters

        if "noise_groups" in payload:
            wanted = payload.get("noise_groups") or []
            # On ne garde que des groupes connus : une faute de frappe côté
            # client désactiverait sinon le filtre sans rien signaler.
            filters.noise_groups = [
                name for name in wanted if name in noise.GROUPS
            ]
        if "exclude_words" in payload:
            filters.exclude_words = [
                word.strip()
                for word in (payload.get("exclude_words") or [])
                if str(word).strip()
            ]

        engine.refresh_filters()
        await asyncio.to_thread(engine.config.save)
        data = _filters_payload(engine)
        engine.bus.publish("filters", data)
        return JSONResponse(data)

    @app.post("/api/config/save")
    async def save_config() -> JSONResponse:
        path = await asyncio.to_thread(engine.config.save)
        return JSONResponse({"saved": str(path)})

    # ── Flux temps réel ───────────────────────────────────────────────────
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            await websocket.send_json(
                {"type": "snapshot", "data": await build_snapshot(engine)}
            )
            async for message in engine.bus.subscribe():
                await websocket.send_json(message)
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        except Exception:
            log.debug("websocket fermé", exc_info=True)

    return app


class DashboardServer:
    """Enveloppe uvicorn avec un arrêt propre.

    Annuler la tâche `serve()` fait remonter un `CancelledError` depuis le
    lifespan Starlette et pollue la sortie. On passe donc par `should_exit`,
    qui laisse uvicorn fermer ses connexions puis rendre la main.
    """

    def __init__(self, app: FastAPI, host: str, port: int) -> None:
        import uvicorn

        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
            timeout_graceful_shutdown=3,
        )
        self._server = uvicorn.Server(config)
        # C'est le CLI qui pilote l'arrêt global (moteur + serveur), pas uvicorn.
        self._server.install_signal_handlers = lambda: None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._server.serve(), name="dashboard")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._server.should_exit = True
        try:
            await asyncio.wait_for(self._task, timeout=6)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._task.cancel()
        self._task = None
