"""Bus d'événements pub/sub pour alimenter dashboard et notificateurs.

Chaque abonné a sa propre file bornée : un client WebSocket lent ne peut
pas ralentir le moteur de scan. En cas de saturation, on jette le plus
ancien événement de CE client uniquement.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

log = logging.getLogger(__name__)


class EventBus:
    def __init__(self, max_queue: int = 256) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._max_queue = max_queue

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event_type: str, data: Any) -> None:
        """Diffuse sans jamais bloquer l'appelant."""
        if not self._subscribers:
            return
        message = {"type": event_type, "data": data}
        for queue in self._subscribers:
            if queue.full():
                try:
                    queue.get_nowait()  # évince le plus ancien
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                log.debug("file abonné saturée, événement perdu")

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue)
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)
