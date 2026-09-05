"""API REST + WebSocket qui alimente le dashboard Next.js.

Le WebSocket est le chemin par défaut : une annonce détectée doit apparaître
sans rafraîchissement. Le REST sert l'état initial, l'historique et les
actions (ajouter un mot-clé, mettre en pause…).

Deux règles qui viennent du scanner :

* **Le scanner ne connaît pas cette API.** Il publie sur le bus ; l'API s'y
  abonne. Un client lent ne peut donc rien ralentir en amont.
* **Un client déconnecté ne fait rien tomber.** Chaque WebSocket a sa propre
  file bornée côté bus.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)


def create_app(context: "AppContext") -> FastAPI:
    app = FastAPI(title="BUyee Radar", docs_url="/api/docs", redoc_url=None)

    # Le dashboard Next.js tourne sur un autre port en développement.
    # L'API n'écoute que sur la boucle locale par défaut : ouvrir CORS à
    # localhost n'expose donc rien de plus.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── État ──────────────────────────────────────────────────────────────
    @app.get("/api/health")
    async def health() -> JSONResponse:
        return JSONResponse({
            "ok": True,
            "running": context.scanner._running if context.scanner else False,
            "paused": context.scanner.paused if context.scanner else False,
            "uptime_seconds": round(time.time() - context.started_at),
        })

    @app.get("/api/state")
    async def state() -> JSONResponse:
        return JSONResponse(await context.snapshot())

    @app.get("/api/sources")
    async def sources() -> JSONResponse:
        return JSONResponse(await context.sources())

    @app.get("/api/listings")
    async def listings(
        limit: int = Query(100, ge=1, le=1000),
        source: str = "",
        keyword: str = "",
        min_score: int = Query(0, ge=0, le=100),
        search: str = "",
    ) -> JSONResponse:
        rows = await context.database.search_listings(
            limit=limit, source=source, keyword=keyword,
            min_score=min_score, text=search,
        )
        return JSONResponse(rows)

    @app.get("/api/analytics")
    async def analytics(minutes: int = Query(60, ge=5, le=1440)) -> JSONResponse:
        return JSONResponse(await context.database.analytics(minutes))

    @app.get("/api/system")
    async def system() -> JSONResponse:
        return JSONResponse(await context.system())

    # ── Mots-clés ─────────────────────────────────────────────────────────
    @app.get("/api/keywords")
    async def get_keywords() -> JSONResponse:
        return JSONResponse(context.keywords_payload())

    @app.post("/api/keywords")
    async def add_keyword(payload: dict) -> JSONResponse:
        name = str(payload.get("name") or "").strip()
        if not name:
            return JSONResponse({"error": "nom requis"}, status_code=400)
        try:
            context.upsert_keyword(payload)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        await context.persist()
        return JSONResponse(context.keywords_payload())

    @app.delete("/api/keywords/{name:path}")
    async def remove_keyword(name: str) -> JSONResponse:
        removed = context.remove_keyword(name)
        await context.persist()
        return JSONResponse({"removed": removed, **context.keywords_payload()})

    # ── Pilotage ──────────────────────────────────────────────────────────
    @app.post("/api/scanner/pause")
    async def pause(payload: dict | None = None) -> JSONResponse:
        paused = bool((payload or {}).get("paused", True))
        if context.scanner:
            context.scanner.set_paused(paused)
        return JSONResponse({"paused": paused})

    @app.post("/api/sources/{name}/calibrate")
    async def calibrate_source(name: str) -> JSONResponse:
        """Découvre les sélecteurs d'une source sur une page réelle.

        Ne peut fonctionner que là où buyee.jp est joignable — donc sur la
        machine de l'utilisateur, pas dans un environnement cloisonné.
        """
        result = await context.calibrate(name)
        return JSONResponse(result)

    # ── Flux temps réel ───────────────────────────────────────────────────
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            await websocket.send_json({
                "type": "snapshot", "data": await context.snapshot(), "at": time.time(),
            })
            async for event in context.bus.subscribe():
                await websocket.send_json(event.to_dict())
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        except Exception:
            log.debug("websocket fermé", exc_info=True)

    # ── Dashboard statique (build Next.js exporté) ────────────────────────
    static = Path(__file__).resolve().parents[3] / "frontend" / "out"
    if static.is_dir():
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=static, html=True), name="dashboard")
        log.info("dashboard servi depuis %s", static)

    return app


class DashboardServer:
    """Enveloppe uvicorn avec un arrêt propre.

    Annuler la tâche `serve()` fait remonter un CancelledError depuis le
    lifespan Starlette et pollue la sortie. On passe par `should_exit`.
    """

    def __init__(self, app: FastAPI, host: str, port: int) -> None:
        import uvicorn

        config = uvicorn.Config(
            app, host=host, port=port, log_level="warning",
            access_log=False, timeout_graceful_shutdown=3,
        )
        self._server = uvicorn.Server(config)
        self._server.install_signal_handlers = lambda: None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._server.serve(), name="api")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._server.should_exit = True
        try:
            await asyncio.wait_for(self._task, timeout=6)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._task.cancel()
        self._task = None


class AppContext:
    """Ce que l'API a le droit de voir. Pas de dépendance inverse."""

    def __init__(self, settings, database, bus, scanner=None, adapters=None) -> None:
        self.settings = settings
        self.database = database
        self.bus = bus
        self.scanner = scanner
        self.adapters = adapters or {}
        self.started_at = time.time()

    async def snapshot(self) -> dict[str, Any]:
        base: dict[str, Any] = {
            "uptime_seconds": round(time.time() - self.started_at),
            "keywords": self.keywords_payload()["keywords"],
            "feed": await self.database.recent(60),
            "store": await self.database.stats(),
        }
        if self.scanner is not None:
            base.update(self.scanner.snapshot())
        return base

    async def sources(self) -> list[dict[str, Any]]:
        out = []
        for name, adapter in self.adapters.items():
            support = getattr(adapter, "support", None)
            stats = (
                self.scanner.metrics.source(name).to_dict()
                if self.scanner else {}
            )
            breaker = (
                self.scanner.breakers.get(name).to_dict()
                if self.scanner and name in self.scanner.breakers else {}
            )
            out.append({
                "source": name,
                "label": getattr(adapter, "label", name),
                "support": support.value if support else "unknown",
                "support_note": getattr(adapter, "support_note", ""),
                "enabled": True,
                "stats": stats,
                "breaker": breaker,
            })
        # Les marketplaces connues mais non activées apparaissent aussi :
        # l'utilisateur doit voir ce qui existe et pourquoi c'est éteint.
        from ..adapters.buyee_html import MARKETPLACES
        for name, market in MARKETPLACES.items():
            if name in self.adapters:
                continue
            out.append({
                "source": name, "label": market.label,
                "support": market.support.value,
                "support_note": market.support_note,
                "enabled": False, "stats": {}, "breaker": {},
            })
        return out

    async def system(self) -> dict[str, Any]:
        import platform
        import sqlite3
        import sys

        db_path = Path(self.database.path)
        return {
            "python": platform.python_version(),
            "platform": f"{platform.system()} {platform.release()}",
            "sqlite": sqlite3.sqlite_version,
            "executable": sys.executable,
            "uptime_seconds": round(time.time() - self.started_at),
            "database_path": str(db_path),
            "database_bytes": db_path.stat().st_size if db_path.exists() else 0,
            "memory": _memory(),
            "adapters": len(self.adapters),
            "bus": self.bus.stats(),
        }

    # ── Mots-clés ─────────────────────────────────────────────────────────
    def keywords_payload(self) -> dict[str, Any]:
        hits = {}
        if self.scanner is not None:
            hits = dict(self.scanner.scoring._keyword_hits)
        return {
            "keywords": [
                {**spec.__dict__, "detections": hits.get(spec.name, 0)}
                for spec in self.settings.keywords
            ]
        }

    def upsert_keyword(self, payload: dict) -> None:
        from ..config.loader import KeywordSettings

        name = str(payload["name"]).strip()
        known = set(KeywordSettings.__dataclass_fields__)
        data = {k: v for k, v in payload.items() if k in known}
        data["name"] = name
        spec = KeywordSettings(**data)

        for index, existing in enumerate(self.settings.keywords):
            if existing.name == name:
                self.settings.keywords[index] = spec
                break
        else:
            self.settings.keywords.append(spec)

        if self.scanner is not None:
            from ..app import build_keywords
            self.scanner.set_keywords(build_keywords(self.settings))

    def remove_keyword(self, name: str) -> bool:
        before = len(self.settings.keywords)
        self.settings.keywords = [
            spec for spec in self.settings.keywords if spec.name != name
        ]
        removed = len(self.settings.keywords) < before
        if removed and self.scanner is not None:
            from ..app import build_keywords
            self.scanner.set_keywords(build_keywords(self.settings))
        return removed

    async def persist(self) -> None:
        await asyncio.to_thread(self.settings.save)

    # ── Calibration ───────────────────────────────────────────────────────
    async def calibrate(self, name: str) -> dict[str, Any]:
        import httpx

        from ..adapters.buyee_html import MARKETPLACES, BuyeeAdapter, Selectors
        from ..adapters.calibrate import calibrate as run_calibration
        from ..adapters.base import SearchQuery

        market = MARKETPLACES.get(name)
        if market is None:
            return {"ok": False, "error": f"marketplace inconnue : {name}"}

        probe = BuyeeAdapter(market, selectors=Selectors(item="x", title="x"))
        url = probe.build_url(SearchQuery(text="nike"))
        try:
            async with httpx.AsyncClient(
                timeout=20.0, follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ja-JP,ja;q=0.9"},
            ) as client:
                response = await client.get(url)
            if response.status_code != 200:
                return {
                    "ok": False, "url": url,
                    "error": f"HTTP {response.status_code} — page non récupérée",
                }
            html = response.text
        except Exception as exc:
            return {
                "ok": False, "url": url,
                "error": (
                    f"{type(exc).__name__}: {exc} — la calibration doit tourner "
                    f"sur une machine d'où buyee.jp est joignable"
                ),
            }

        result = run_calibration(html, source=name)
        payload = {
            "ok": result.ok, "url": url, "error": result.error,
            "items_found": result.items_found, "notes": result.notes,
            "selectors": result.selectors.to_dict(), "sample": result.sample,
        }
        if result.ok:
            spec = self.settings.sources.setdefault(
                name, type(next(iter(self.settings.sources.values())))()
                if self.settings.sources else None
            )
            if spec is not None:
                spec.selectors = result.selectors.to_dict()
                spec.enabled = True
                await self.persist()
        return payload


def _memory() -> dict[str, Any]:
    """Mémoire du process. `resource` est POSIX ; sous Windows on renvoie 0."""
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS compte en octets, Linux en kilo-octets.
        import sys
        factor = 1 if sys.platform == "darwin" else 1024
        return {"rss_bytes": usage * factor}
    except Exception:
        return {"rss_bytes": 0}
