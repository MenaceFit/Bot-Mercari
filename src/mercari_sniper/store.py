"""Persistance SQLite des annonces détectées.

Le bot v1 réécrivait tout `seen_items.json` à chaque scan (15 000 entrées
sérialisées en JSON, plusieurs fois par minute). Ici :
  * la déduplication est en mémoire (set) => O(1) sans I/O sur le hot path ;
  * les écritures SQLite sont groupées et déportées dans un thread, donc la
    boucle asyncio n'est jamais bloquée par le disque.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from collections import deque
from pathlib import Path
from typing import Any, Iterable

from .models import Listing

log = logging.getLogger(__name__)


def _migrate(conn: sqlite3.Connection) -> None:
    """Ajoute les colonnes manquantes sur une base créée par une version
    antérieure. SQLite n'a pas de `ADD COLUMN IF NOT EXISTS`, on inspecte donc
    le schéma existant avant d'écrire."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(listings)")}
    for column, ddl in (("buyee_url", "TEXT DEFAULT ''"),):
        if column not in existing:
            conn.execute(f"ALTER TABLE listings ADD COLUMN {column} {ddl}")
            log.info("base migrée : colonne %s ajoutée", column)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    price         INTEGER NOT NULL DEFAULT 0,
    url           TEXT NOT NULL,
    image         TEXT DEFAULT '',
    seller_id     TEXT DEFAULT '',
    source        TEXT DEFAULT '',
    matched       TEXT DEFAULT '[]',
    rarity        TEXT DEFAULT '',
    created       INTEGER DEFAULT 0,
    detected_at   REAL DEFAULT 0,
    latency_ms    INTEGER DEFAULT 0,
    buyee_url     TEXT DEFAULT '',
    notified      INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_listings_detected ON listings(detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_listings_created  ON listings(created DESC);

CREATE TABLE IF NOT EXISTS seen (
    id      TEXT PRIMARY KEY,
    seen_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_seen_at ON seen(seen_at);
"""


class Store:
    """Accès SQLite thread-safe, non bloquant pour la boucle d'événements."""

    def __init__(self, path: str | Path, *, retention_days: int = 30) -> None:
        self.path = Path(path)
        self.retention_days = retention_days
        self._conn: sqlite3.Connection | None = None
        self._write_lock = asyncio.Lock()
        self._pending: deque[Listing] = deque()

    # ── Cycle de vie ──────────────────────────────────────────────────────
    async def open(self) -> None:
        await asyncio.to_thread(self._open_sync)

    def _open_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10.0)
        conn.row_factory = sqlite3.Row
        # WAL : les lectures du dashboard ne bloquent pas les écritures du scan.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(_SCHEMA)
        _migrate(conn)
        conn.commit()
        self._conn = conn
        log.info("base ouverte: %s", self.path)

    async def close(self) -> None:
        await self.flush()
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None

    @property
    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Store.open() n'a pas été appelé")
        return self._conn

    # ── Amorçage du cache de déduplication ────────────────────────────────
    async def load_seen_ids(self, limit: int = 100_000) -> set[str]:
        return await asyncio.to_thread(self._load_seen_ids_sync, limit)

    def _load_seen_ids_sync(self, limit: int) -> set[str]:
        rows = self._db.execute(
            "SELECT id FROM seen ORDER BY seen_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return {row["id"] for row in rows}

    async def mark_seen_bulk(self, ids: Iterable[str]) -> None:
        """Enregistre des ids sans notification (warmup, import v1)."""
        payload = [(item_id, time.time()) for item_id in ids]
        if not payload:
            return
        async with self._write_lock:
            await asyncio.to_thread(self._mark_seen_sync, payload)

    def _mark_seen_sync(self, payload: list[tuple[str, float]]) -> None:
        self._db.executemany(
            "INSERT OR IGNORE INTO seen (id, seen_at) VALUES (?, ?)", payload
        )
        self._db.commit()

    # ── Écriture des annonces ─────────────────────────────────────────────
    def queue(self, listing: Listing) -> None:
        """Met en file pour un flush groupé. Ne touche pas au disque."""
        self._pending.append(listing)

    async def flush(self) -> None:
        if not self._pending or self._conn is None:
            return
        batch = list(self._pending)
        self._pending.clear()
        async with self._write_lock:
            await asyncio.to_thread(self._flush_sync, batch)

    def _flush_sync(self, batch: list[Listing]) -> None:
        rows = [
            (
                listing.id,
                listing.title,
                listing.price,
                listing.url,
                listing.image,
                listing.seller_id,
                listing.source,
                json.dumps(listing.matched, ensure_ascii=False),
                listing.rarity,
                listing.created,
                listing.detected_at,
                listing.latency_ms,
                listing.buyee_url,
            )
            for listing in batch
        ]
        with self._db:  # transaction unique pour tout le lot
            self._db.executemany(
                """INSERT OR IGNORE INTO listings
                   (id, title, price, url, image, seller_id, source, matched,
                    rarity, created, detected_at, latency_ms, buyee_url)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
            self._db.executemany(
                "INSERT OR IGNORE INTO seen (id, seen_at) VALUES (?, ?)",
                [(listing.id, listing.detected_at) for listing in batch],
            )

    # ── Lecture ───────────────────────────────────────────────────────────
    async def recent(
        self, limit: int = 100, keyword: str | None = None
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._recent_sync, limit, keyword)

    def _recent_sync(self, limit: int, keyword: str | None) -> list[dict[str, Any]]:
        if keyword:
            rows = self._db.execute(
                """SELECT * FROM listings
                   WHERE matched LIKE ? ORDER BY detected_at DESC LIMIT ?""",
                (f"%{keyword}%", limit),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM listings ORDER BY detected_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        try:
            data["matched"] = json.loads(data.get("matched") or "[]")
        except json.JSONDecodeError:
            data["matched"] = []
        return data

    async def stats(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._stats_sync)

    def _stats_sync(self) -> dict[str, Any]:
        day_ago = time.time() - 86400
        row = self._db.execute(
            """SELECT COUNT(*) AS total,
                      AVG(NULLIF(latency_ms, 0)) AS avg_latency,
                      MIN(NULLIF(latency_ms, 0)) AS best_latency
               FROM listings"""
        ).fetchone()
        today = self._db.execute(
            "SELECT COUNT(*) AS n FROM listings WHERE detected_at >= ?", (day_ago,)
        ).fetchone()
        seen = self._db.execute("SELECT COUNT(*) AS n FROM seen").fetchone()
        return {
            "total_listings": row["total"] or 0,
            "listings_24h": today["n"] or 0,
            "seen_cache": seen["n"] or 0,
            "avg_latency_ms": round(row["avg_latency"] or 0),
            "best_latency_ms": row["best_latency"] or 0,
        }

    # ── Entretien ─────────────────────────────────────────────────────────
    async def prune(self) -> int:
        return await asyncio.to_thread(self._prune_sync)

    def _prune_sync(self) -> int:
        cutoff = time.time() - self.retention_days * 86400
        with self._db:
            deleted = self._db.execute(
                "DELETE FROM listings WHERE detected_at < ?", (cutoff,)
            ).rowcount
            self._db.execute("DELETE FROM seen WHERE seen_at < ?", (cutoff,))
        if deleted:
            log.info("purge: %d annonces supprimées", deleted)
        return deleted
