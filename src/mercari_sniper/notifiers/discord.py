"""Notificateur Discord asynchrone.

Différence clé avec la v1 : l'envoi ne bloque plus le scan. Le bot d'origine
faisait `requests.post()` + `time.sleep(0.8)` **dans la boucle de scan** — avec
20 annonces trouvées, la détection s'arrêtait 16 secondes. Ici un worker
dédié dépile en tâche de fond pendant que le scan continue.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from ..matching import PREMIUM, RARE, ULTRA
from ..models import Listing

log = logging.getLogger(__name__)

_RARITY_EMOJI = {
    ULTRA[0]: "🔥",
    RARE[0]: "⭐",
    PREMIUM[0]: "💎",
}
_RARITY_ORDER = {PREMIUM[0]: 0, RARE[0]: 1, ULTRA[0]: 2}


class DiscordNotifier:
    """File d'envoi bornée + respect du rate limit Discord (429/Retry-After)."""

    name = "discord"

    def __init__(
        self,
        webhook_url: str,
        *,
        rate_limit: float = 2.0,
        max_queue: int = 500,
        min_rarity: str = PREMIUM[0],
        timeout: float = 10.0,
    ) -> None:
        self.webhook_url = webhook_url
        self._interval = 1.0 / max(0.1, rate_limit)
        self._queue: asyncio.Queue[Listing] = asyncio.Queue(maxsize=max_queue)
        self._min_rarity = _RARITY_ORDER.get(min_rarity, 0)
        self._client = httpx.AsyncClient(timeout=timeout)
        self._task: asyncio.Task | None = None
        self.sent = 0
        self.failed = 0
        self.dropped = 0

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._worker(), name="discord-notifier")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._client.aclose()

    def notify(self, listing: Listing) -> None:
        """Met en file. Ne bloque jamais — c'est appelé depuis le hot path."""
        if not self.webhook_url:
            return
        if _RARITY_ORDER.get(listing.rarity, 0) < self._min_rarity:
            return
        try:
            self._queue.put_nowait(listing)
        except asyncio.QueueFull:
            self.dropped += 1
            log.warning("file Discord pleine, annonce non notifiée: %s", listing.id)

    async def _worker(self) -> None:
        while True:
            listing = await self._queue.get()
            try:
                await self._send(listing)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.failed += 1
                log.exception("échec notification Discord %s", listing.id)
            finally:
                self._queue.task_done()
            await asyncio.sleep(self._interval)

    async def _send(self, listing: Listing) -> None:
        payload = {
            "username": "⚡ Mercari Sniper",
            "avatar_url": "https://static.mercdn.net/img/favicon/favicon.ico",
            "embeds": [self._build_embed(listing)],
        }

        for attempt in range(3):
            response = await self._client.post(self.webhook_url, json=payload)

            if response.status_code in (200, 204):
                self.sent += 1
                return

            if response.status_code == 429:
                try:
                    retry_after = float(response.json().get("retry_after", 2.0))
                except Exception:
                    retry_after = 2.0
                # Discord renvoie parfois des millisecondes sur les vieux webhooks.
                if retry_after > 100:
                    retry_after /= 1000.0
                log.info("Discord 429, pause %.1fs", retry_after)
                await asyncio.sleep(retry_after + 0.25)
                continue

            if 500 <= response.status_code < 600 and attempt < 2:
                await asyncio.sleep(1.0 * (attempt + 1))
                continue

            self.failed += 1
            log.warning(
                "Discord %s: %s", response.status_code, response.text[:160]
            )
            return

        self.failed += 1

    def _build_embed(self, listing: Listing) -> dict:
        emoji = _RARITY_EMOJI.get(listing.rarity, "💎")
        tags = " • ".join(f"`{kw}`" for kw in listing.matched[:4])
        if len(listing.matched) > 4:
            tags += f" *(+{len(listing.matched) - 4})*"

        latency = listing.latency_ms
        speed = f"{latency / 1000:.1f}s" if latency else "n/a"

        embed = {
            "title": f"{emoji} {listing.rarity}  ·  {listing.title[:200]}",
            "url": listing.url,
            "color": listing.rarity_color,
            "description": tags or "—",
            "fields": [
                {
                    "name": "💰 Prix",
                    "value": f"¥{listing.price:,}",
                    "inline": True,
                },
                {
                    "name": "⚡ Détecté en",
                    "value": speed,
                    "inline": True,
                },
                {
                    "name": "🛍️ Annonce",
                    "value": f"[Voir sur Mercari JP]({listing.url})",
                    "inline": True,
                },
            ],
            "footer": {
                "text": f"⚡ Mercari Sniper · {datetime.now().strftime('%d/%m %H:%M:%S')}"
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if listing.image.startswith("http"):
            embed["image"] = {"url": listing.image}
        return embed

    def stats(self) -> dict:
        return {
            "sent": self.sent,
            "failed": self.failed,
            "dropped": self.dropped,
            "queued": self._queue.qsize(),
        }
