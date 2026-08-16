"""API REST + WebSocket alimentant le dashboard local."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from .engine import SniperEngine

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent / "web"


def create_app(engine: SniperEngine) -> FastAPI:
    app = FastAPI(title="Mercari Sniper", docs_url=None, redoc_url=None)

    # ── Dashboard ─────────────────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    async def dashboard() -> HTMLResponse:
        index = WEB_DIR / "index.html"
        if not index.exists():
            return HTMLResponse("<h1>Dashboard introuvable</h1>", status_code=500)
        return HTMLResponse(index.read_text("utf-8"))

    # ── État ──────────────────────────────────────────────────────────────
    @app.get("/api/state")
    async def state() -> JSONResponse:
        store_stats = await engine.store.stats()
        return JSONResponse(
            {
                "stats": {**engine.stats(), **store_stats},
                "sources": engine.sources_state(),
                "keywords": engine.config.keywords,
                "feed": list(engine.feed),
                "backend": engine.backend.name,
            }
        )

    @app.get("/api/listings")
    async def listings(limit: int = 100, keyword: str | None = None) -> JSONResponse:
        limit = max(1, min(1000, limit))
        return JSONResponse(await engine.store.recent(limit, keyword))

    @app.get("/api/health")
    async def health() -> JSONResponse:
        return JSONResponse({"ok": True, "running": engine.running})

    # ── Pilotage ──────────────────────────────────────────────────────────
    @app.post("/api/keywords")
    async def add_keyword(payload: dict) -> JSONResponse:
        keyword = str(payload.get("keyword", "")).strip()
        if not keyword:
            return JSONResponse({"error": "keyword requis"}, status_code=400)

        added = engine.add_keyword(keyword)
        # Un keyword dont la racine n'est couverte par aucune source ne serait
        # jamais vu : on lui crée sa propre source.
        root = keyword.split()[0]
        if added and not any(
            root == s.config.query or s.config.query in keyword
            for s in engine._sources.values()
        ):
            await engine.add_source(keyword, page_size=60)

        engine.bus.publish("keywords", engine.config.keywords)
        return JSONResponse({"added": added, "keywords": engine.config.keywords})

    @app.delete("/api/keywords/{keyword:path}")
    async def remove_keyword(keyword: str) -> JSONResponse:
        removed = engine.remove_keyword(keyword)
        engine.bus.publish("keywords", engine.config.keywords)
        return JSONResponse({"removed": removed, "keywords": engine.config.keywords})

    @app.post("/api/sources/{query:path}/pause")
    async def pause_source(query: str, payload: dict | None = None) -> JSONResponse:
        paused = bool((payload or {}).get("paused", True))
        ok = engine.set_source_paused(query, paused)
        return JSONResponse({"ok": ok, "paused": paused})

    @app.post("/api/config/save")
    async def save_config() -> JSONResponse:
        path = engine.config.save()
        return JSONResponse({"saved": str(path)})

    # ── Flux temps réel ───────────────────────────────────────────────────
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            store_stats = await engine.store.stats()
            await websocket.send_json(
                {
                    "type": "snapshot",
                    "data": {
                        "stats": {**engine.stats(), **store_stats},
                        "sources": engine.sources_state(),
                        "keywords": engine.config.keywords,
                        "feed": list(engine.feed),
                        "backend": engine.backend.name,
                    },
                }
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
