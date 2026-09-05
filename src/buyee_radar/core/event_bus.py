"""Bus d'événements : le scanner publie, tout le monde écoute.

Le scanner ne connaît ni le dashboard, ni les notifieurs. Il publie un
événement et repart. C'est ce qui garantit qu'un client WebSocket lent ne
peut pas ralentir la détection.

Chaque abonné a sa **propre file bornée**. Quand elle déborde, c'est cet
abonné-là qui perd des événements, pas les autres, et surtout pas le
scanner. Un onglet de dashboard laissé ouvert sur une machine en veille ne
doit pas pouvoir bloquer le bot.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Event:
    type: str
    data: Any
    at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "data": self.data, "at": self.at}


class EventBus:
    """Diffusion un-vers-plusieurs, sans retour de pression sur l'émetteur."""

    def __init__(self, *, queue_size: int = 256, replay: int = 50) -> None:
        self.queue_size = queue_size
        self._subscribers: set[asyncio.Queue] = set()
        #: Petit historique rejoué à la connexion : un dashboard qui s'ouvre
        #: doit voir les dernières trouvailles, pas un écran vide en attendant
        #: la suivante.
        self._recent: deque[Event] = deque(maxlen=replay)
        self.published = 0
        self.dropped = 0

    def publish(self, event_type: str, data: Any) -> None:
        """Publie. SYNCHRONE et NON BLOQUANT — appelable du chemin critique."""
        event = Event(type=event_type, data=data)
        self.published += 1
        self._recent.append(event)

        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Cet abonné-là ne suit pas : il perd son événement le plus
                # ancien. Les autres et le scanner ne sont pas affectés.
                self.dropped += 1
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    async def subscribe(self, replay: bool = True) -> AsyncIterator[Event]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.queue_size)
        self._subscribers.add(queue)
        try:
            if replay:
                for event in list(self._recent):
                    try:
                        queue.put_nowait(event)
                    except asyncio.QueueFull:
                        break
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    @property
    def subscribers(self) -> int:
        return len(self._subscribers)

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return [event.to_dict() for event in list(self._recent)[-limit:]]

    def stats(self) -> dict[str, Any]:
        return {
            "subscribers": self.subscribers,
            "published": self.published,
            "dropped": self.dropped,
        }
