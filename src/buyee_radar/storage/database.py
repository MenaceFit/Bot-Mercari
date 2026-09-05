"""Persistance SQLite.

Deux règles qui viennent de l'audit de l'ancien bot :

1. **Jamais de JSON réécrit à chaque annonce.** L'ancienne version
   sérialisait tout son cache à chaque trouvaille ; le coût grandissait avec
   l'historique, dans la boucle de scan.
2. **Aucune I/O sur la boucle asyncio.** `sqlite3` est synchrone et bloquant.
   Les écritures sont regroupées et exécutées dans un thread, de sorte que le
   scanner ne s'arrête jamais pour attendre le disque.

Le mode WAL permet à un lecteur (le dashboard) de travailler pendant que le
scanner écrit.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

from ..adapters.base import Listing

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    key           TEXT PRIMARY KEY,      -- source:listing_id
    listing_id    TEXT NOT NULL,
    source        TEXT NOT NULL,
    title         TEXT NOT NULL,
    url           TEXT NOT NULL,
    buy_url       TEXT DEFAULT '',
    price         INTEGER DEFAULT 0,
    currency      TEXT DEFAULT 'JPY',
    price_eur     REAL DEFAULT 0,
    image_url     TEXT DEFAULT '',
    seller        TEXT DEFAULT '',
    category      TEXT DEFAULT '',
    condition     TEXT DEFAULT '',
    brand         TEXT DEFAULT '',
    keyword       TEXT DEFAULT '',
    keywords      TEXT DEFAULT '',
    score         INTEGER DEFAULT 0,
    tier          TEXT DEFAULT 'NORMAL',
    created_at    REAL DEFAULT 0,        -- T0, publication marketplace
    detected_at   REAL DEFAULT 0,        -- T3, réception
    latency_ms    INTEGER DEFAULT 0,     -- T0 -> T3
    end_to_end_ms INTEGER DEFAULT 0,     -- T0 -> T5
    stored_at     REAL NOT NULL
);
-- L'historique se consulte par date, par source, par score et par mot-clé :
-- un index par axe de filtre de la page History.
CREATE INDEX IF NOT EXISTS idx_listings_stored  ON listings(stored_at DESC);
CREATE INDEX IF NOT EXISTS idx_listings_source  ON listings(source, stored_at DESC);
CREATE INDEX IF NOT EXISTS idx_listings_score   ON listings(score DESC, stored_at DESC);
CREATE INDEX IF NOT EXISTS idx_listings_keyword ON listings(keyword, stored_at DESC);
CREATE INDEX IF NOT EXISTS idx_listings_price   ON listings(price);

-- Table volontairement minuscule : elle ne sert qu'à répondre « déjà vue ? »
-- au redémarrage. La séparer de `listings` évite de charger des titres et
-- des URL pour amorcer un cache d'identifiants.
CREATE TABLE IF NOT EXISTS seen_listings (
    key       TEXT PRIMARY KEY,
    seen_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_seen_at ON seen_listings(seen_at DESC);

CREATE TABLE IF NOT EXISTS detections (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT NOT NULL,
    keyword    TEXT NOT NULL,
    source     TEXT NOT NULL,
    score      INTEGER DEFAULT 0,
    latency_ms INTEGER DEFAULT 0,
    at         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_detections_at ON detections(at DESC);
CREATE INDEX IF NOT EXISTS idx_detections_kw ON detections(keyword, at DESC);

CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT NOT NULL,
    channel    TEXT NOT NULL,
    ok         INTEGER NOT NULL,
    latency_ms INTEGER DEFAULT 0,
    error      TEXT DEFAULT '',
    sent_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notif_sent ON notifications(sent_at DESC);

CREATE TABLE IF NOT EXISTS keywords (
    name       TEXT PRIMARY KEY,
    config     TEXT NOT NULL,          -- JSON
    enabled    INTEGER DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    name        TEXT PRIMARY KEY,
    label       TEXT DEFAULT '',
    support     TEXT DEFAULT '',
    selectors   TEXT DEFAULT '',        -- JSON, issu de la calibration
    last_ok     REAL DEFAULT 0,
    last_error  TEXT DEFAULT '',
    ok_count    INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    updated_at  REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS scanner_metrics (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL,
    value REAL NOT NULL,
    tags  TEXT DEFAULT '',
    at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metrics_at ON scanner_metrics(name, at DESC);

CREATE TABLE IF NOT EXISTS system_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    level   TEXT NOT NULL,
    source  TEXT DEFAULT '',
    event   TEXT NOT NULL,
    detail  TEXT DEFAULT '',
    at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_at ON system_events(at DESC);
"""


class Database:
    """Base locale, écrite par lots hors de la boucle d'événements."""

    def __init__(self, path: str | Path, flush_every: int = 50) -> None:
        self.path = Path(path)
        self.flush_every = flush_every
        self._conn: sqlite3.Connection | None = None
        self._pending: list[Listing] = []
        self._pending_notifs: list[tuple] = []
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await asyncio.to_thread(self._connect)
        log.info("base ouverte : %s", self.path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        # WAL : le dashboard peut lire pendant que le scanner écrit.
        conn.execute("PRAGMA journal_mode=WAL")
        # NORMAL plutôt que FULL : on accepte de perdre la toute dernière
        # transaction sur coupure de courant. Le coût de FULL est un fsync
        # par écriture, et une annonce perdue se retrouve au scan suivant.
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
        conn.commit()
        return conn

    async def close(self) -> None:
        await self.flush()
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None

    # ── Écritures ─────────────────────────────────────────────────────────
    def queue(self, listing: Listing) -> None:
        """Met une annonce en file. Ne touche jamais le disque ici."""
        self._pending.append(listing)

    def queue_notification(
        self, key: str, channel: str, ok: bool, latency_ms: float, error: str = ""
    ) -> None:
        self._pending_notifs.append(
            (key, channel, 1 if ok else 0, int(latency_ms), error[:300], time.time())
        )

    @property
    def pending(self) -> int:
        return len(self._pending) + len(self._pending_notifs)

    async def flush(self) -> int:
        """Écrit le lot en attente. Renvoie le nombre d'annonces écrites."""
        async with self._lock:
            if self._conn is None:
                return 0
            listings, self._pending = self._pending, []
            notifs, self._pending_notifs = self._pending_notifs, []
            if not listings and not notifs:
                return 0
            await asyncio.to_thread(self._write, listings, notifs)
            return len(listings)

    def _write(self, listings: list[Listing], notifs: list[tuple]) -> None:
        assert self._conn is not None
        now = time.time()
        rows = [
            (
                item.key, item.listing_id, item.source, item.title, item.url,
                item.buy_url, item.price, item.currency, item.price_eur,
                item.image_url, item.seller, item.category, item.condition,
                item.brand, item.keyword,
                json.dumps(item.keywords, ensure_ascii=False),
                item.score, item.tier,
                item.created_at, item.detected_at,
                item.latency_ms, item.end_to_end_ms, now,
            )
            for item in listings
        ]
        detections = [
            (item.key, item.keyword, item.source, item.score, item.latency_ms, now)
            for item in listings
        ]
        # Une seule transaction pour tout le lot : ouvrir et fermer une
        # transaction par annonce coûterait plus cher que l'écriture.
        with self._conn:
            if rows:
                self._conn.executemany(
                    "INSERT OR IGNORE INTO listings VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    rows,
                )
                self._conn.executemany(
                    "INSERT OR IGNORE INTO seen_listings (key, seen_at) VALUES (?,?)",
                    [(item.key, now) for item in listings],
                )
                self._conn.executemany(
                    "INSERT INTO detections "
                    "(key, keyword, source, score, latency_ms, at) "
                    "VALUES (?,?,?,?,?,?)",
                    detections,
                )
            if notifs:
                self._conn.executemany(
                    "INSERT INTO notifications "
                    "(key, channel, ok, latency_ms, error, sent_at) "
                    "VALUES (?,?,?,?,?,?)",
                    notifs,
                )

    async def mark_seen(self, keys: Iterable[str]) -> None:
        """Marque des annonces comme vues sans les enregistrer intégralement.

        C'est ce qui permet au premier scan de mémoriser tout ce qui est déjà
        en ligne sans notifier ni stocker des centaines d'annonces qui ne
        correspondent à rien.
        """
        keys = list(keys)
        if not keys or self._conn is None:
            return
        now = time.time()
        await asyncio.to_thread(self._mark_seen, keys, now)

    def _mark_seen(self, keys: list[str], now: float) -> None:
        assert self._conn is not None
        with self._conn:
            self._conn.executemany(
                "INSERT OR IGNORE INTO seen_listings (key, seen_at) VALUES (?,?)",
                [(key, now) for key in keys],
            )

    # ── Lectures ──────────────────────────────────────────────────────────
    async def load_seen(self, limit: int = 100_000) -> set[str]:
        if self._conn is None:
            return set()
        return await asyncio.to_thread(self._load_seen, limit)

    def _load_seen(self, limit: int) -> set[str]:
        assert self._conn is not None
        cur = self._conn.execute(
            "SELECT key FROM seen_listings ORDER BY seen_at DESC LIMIT ?", (limit,)
        )
        return {row[0] for row in cur.fetchall()}

    async def recent(self, limit: int = 100, source: str = "") -> list[dict[str, Any]]:
        if self._conn is None:
            return []
        return await asyncio.to_thread(self._recent, limit, source)

    def _recent(self, limit: int, source: str) -> list[dict[str, Any]]:
        assert self._conn is not None
        sql = "SELECT * FROM listings"
        params: list[Any] = []
        if source:
            sql += " WHERE source = ?"
            params.append(source)
        sql += " ORDER BY stored_at DESC LIMIT ?"
        params.append(limit)

        cur = self._conn.execute(sql, params)
        columns = [d[0] for d in cur.description]
        out = []
        for row in cur.fetchall():
            record = dict(zip(columns, row))
            try:
                record["keywords"] = json.loads(record.get("keywords") or "[]")
            except json.JSONDecodeError:
                record["keywords"] = []
            out.append(record)
        return out

    async def stats(self) -> dict[str, Any]:
        if self._conn is None:
            return {}
        return await asyncio.to_thread(self._stats)

    def _stats(self) -> dict[str, Any]:
        assert self._conn is not None
        day_ago = time.time() - 86400
        total = self._conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        recent = self._conn.execute(
            "SELECT COUNT(*) FROM listings WHERE stored_at > ?", (day_ago,)
        ).fetchone()[0]
        seen = self._conn.execute("SELECT COUNT(*) FROM seen_listings").fetchone()[0]
        return {"listings_total": total, "listings_24h": recent, "seen_total": seen}

    async def search_listings(
        self, *, limit: int = 100, source: str = "", keyword: str = "",
        min_score: int = 0, text: str = "",
    ) -> list[dict[str, Any]]:
        """Historique filtré. Chaque axe de filtre a son index (voir SCHEMA)."""
        if self._conn is None:
            return []
        return await asyncio.to_thread(
            self._search, limit, source, keyword, min_score, text
        )

    def _search(
        self, limit: int, source: str, keyword: str, min_score: int, text: str
    ) -> list[dict[str, Any]]:
        assert self._conn is not None
        clauses, params = [], []
        if source:
            clauses.append("source = ?")
            params.append(source)
        if keyword:
            clauses.append("keyword = ?")
            params.append(keyword)
        if min_score:
            clauses.append("score >= ?")
            params.append(min_score)
        if text:
            # LIKE plutôt que FTS : l'historique local se compte en dizaines
            # de milliers de lignes, pas en millions, et une table FTS
            # coûterait une écriture de plus sur le chemin critique.
            clauses.append("title LIKE ?")
            params.append(f"%{text}%")

        sql = "SELECT * FROM listings"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY stored_at DESC LIMIT ?"
        params.append(max(1, min(1000, limit)))

        cur = self._conn.execute(sql, params)
        columns = [d[0] for d in cur.description]
        out = []
        for row in cur.fetchall():
            record = dict(zip(columns, row))
            try:
                record["keywords"] = json.loads(record.get("keywords") or "[]")
            except json.JSONDecodeError:
                record["keywords"] = []
            out.append(record)
        return out

    async def analytics(self, minutes: int = 60) -> dict[str, Any]:
        if self._conn is None:
            return {}
        return await asyncio.to_thread(self._analytics, minutes)

    def _analytics(self, minutes: int) -> dict[str, Any]:
        assert self._conn is not None
        since = time.time() - minutes * 60
        bucket = 60 if minutes <= 120 else 300

        rows = self._conn.execute(
            "SELECT CAST(at / ? AS INTEGER) * ? AS slot, COUNT(*), "
            "AVG(NULLIF(latency_ms, 0)) "
            "FROM detections WHERE at > ? GROUP BY slot ORDER BY slot",
            (bucket, bucket, since),
        ).fetchall()
        timeline = [
            {"at": row[0], "detections": row[1], "avg_latency_ms": round(row[2] or 0)}
            for row in rows
        ]

        by_source = [
            {"source": row[0], "detections": row[1]}
            for row in self._conn.execute(
                "SELECT source, COUNT(*) FROM detections WHERE at > ? "
                "GROUP BY source ORDER BY 2 DESC", (since,)
            ).fetchall()
        ]
        by_keyword = [
            {"keyword": row[0], "detections": row[1]}
            for row in self._conn.execute(
                "SELECT keyword, COUNT(*) FROM detections WHERE at > ? AND keyword != '' "
                "GROUP BY keyword ORDER BY 2 DESC LIMIT 12", (since,)
            ).fetchall()
        ]
        by_tier = [
            {"tier": row[0], "count": row[1]}
            for row in self._conn.execute(
                "SELECT tier, COUNT(*) FROM listings WHERE stored_at > ? "
                "GROUP BY tier ORDER BY 2 DESC", (since,)
            ).fetchall()
        ]
        notif = self._conn.execute(
            "SELECT COUNT(*), SUM(ok), AVG(latency_ms) FROM notifications WHERE sent_at > ?",
            (since,),
        ).fetchone()

        return {
            "window_minutes": minutes,
            "bucket_seconds": bucket,
            "timeline": timeline,
            "by_source": by_source,
            "by_keyword": by_keyword,
            "by_tier": by_tier,
            "notifications": {
                "total": notif[0] or 0,
                "ok": notif[1] or 0,
                "avg_ms": round(notif[2] or 0),
            },
        }

    async def purge(self, retention_days: int = 30) -> int:
        """Supprime l'historique ancien. Renvoie le nombre de lignes effacées."""
        if self._conn is None or retention_days <= 0:
            return 0
        cutoff = time.time() - retention_days * 86400
        return await asyncio.to_thread(self._purge, cutoff)

    def _purge(self, cutoff: float) -> int:
        assert self._conn is not None
        with self._conn:
            deleted = self._conn.execute(
                "DELETE FROM listings WHERE stored_at < ?", (cutoff,)
            ).rowcount
            self._conn.execute(
                "DELETE FROM seen_listings WHERE seen_at < ?", (cutoff,)
            )
            self._conn.execute(
                "DELETE FROM notifications WHERE sent_at < ?", (cutoff,)
            )
        return max(0, deleted)
