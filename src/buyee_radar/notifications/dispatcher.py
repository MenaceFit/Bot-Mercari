"""Contrat des notifieurs, et la file qui les découple du scanner.

Règle non négociable du cahier des charges : **le scanner ne doit jamais
attendre une notification**. C'est ce qui a tué la première version du bot —
un `requests.post()` suivi d'un `sleep(0.8)` dans la boucle de scan, soit
seize secondes d'aveuglement pour vingt trouvailles.

Le découplage tient en trois points :

* `dispatch()` est **synchrone et non bloquant** : il pose l'annonce dans une
  file et rend la main immédiatement.
* Chaque canal a son propre worker : Telegram qui rame ne retarde pas Discord.
* La file est **bornée**. Une file non bornée devant un canal en panne
  consomme la mémoire jusqu'au crash ; on préfère jeter les plus anciennes
  notifications et le dire, plutôt que d'emporter le scanner avec.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..adapters.base import Listing

log = logging.getLogger(__name__)


@runtime_checkable
class Notifier(Protocol):
    """Un canal de sortie."""

    name: str
    enabled: bool

    async def send(self, listing: Listing) -> None:
        """Envoie. Lève en cas d'échec — le worker gère la reprise."""
        ...

    async def close(self) -> None:
        ...


@dataclass
class NotifierStats:
    sent: int = 0
    failed: int = 0
    dropped: int = 0
    retries: int = 0
    last_error: str = ""
    latencies: deque[float] = field(default_factory=lambda: deque(maxlen=500))

    @property
    def avg_ms(self) -> float:
        return sum(self.latencies) / len(self.latencies) if self.latencies else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "sent": self.sent,
            "failed": self.failed,
            "dropped": self.dropped,
            "retries": self.retries,
            "avg_ms": round(self.avg_ms, 1),
            "last_error": self.last_error,
        }


class NotificationHub:
    """Une file et un worker par canal. Le scanner ne voit que `dispatch()`."""

    def __init__(
        self,
        notifiers: list[Notifier] | None = None,
        *,
        max_queue: int = 500,
        max_retries: int = 3,
        dry_run: bool = False,
        on_sent=None,
    ) -> None:
        self.notifiers = [n for n in (notifiers or []) if n.enabled]
        self.max_queue = max_queue
        self.max_retries = max_retries
        self.dry_run = dry_run
        self.on_sent = on_sent
        self.stats: dict[str, NotifierStats] = {
            n.name: NotifierStats() for n in self.notifiers
        }
        self._queues: dict[str, asyncio.Queue] = {}
        self._workers: list[asyncio.Task] = []
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        for notifier in self.notifiers:
            queue: asyncio.Queue = asyncio.Queue(maxsize=self.max_queue)
            self._queues[notifier.name] = queue
            self._workers.append(
                asyncio.create_task(
                    self._worker(notifier, queue), name=f"notify:{notifier.name}"
                )
            )
        if self.notifiers:
            log.info(
                "notifications actives : %s%s",
                ", ".join(n.name for n in self.notifiers),
                "  (mode simulation, rien ne part)" if self.dry_run else "",
            )

    def dispatch(self, listing: Listing) -> None:
        """Met l'annonce en file pour tous les canaux. NE BLOQUE JAMAIS.

        Appelée depuis le chemin critique : pas d'`await`, pas d'I/O, pas
        d'exception qui remonte. Une file pleine coûte la notification la
        plus ancienne, jamais le scan en cours.
        """
        for notifier in self.notifiers:
            queue = self._queues.get(notifier.name)
            if queue is None:
                continue
            try:
                queue.put_nowait(listing)
            except asyncio.QueueFull:
                stats = self.stats[notifier.name]
                stats.dropped += 1
                try:
                    queue.get_nowait()          # jette la plus ancienne
                    queue.task_done()
                    queue.put_nowait(listing)   # garde la plus récente
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
                if stats.dropped % 25 == 1:
                    log.warning(
                        "file %s saturée (%d abandons) — le canal ne suit pas, "
                        "le scan continue",
                        notifier.name, stats.dropped,
                    )

    async def _worker(self, notifier: Notifier, queue: asyncio.Queue) -> None:
        stats = self.stats[notifier.name]
        try:
            while self._running:
                listing = await queue.get()
                try:
                    await self._deliver(notifier, listing, stats)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Un notifieur ne doit JAMAIS tuer son worker : la panne
                    # d'un canal rendrait le bot muet sur tous les autres.
                    log.exception("%s : échec non rattrapé", notifier.name)
                finally:
                    queue.task_done()
        except asyncio.CancelledError:
            pass

    async def _deliver(
        self, notifier: Notifier, listing: Listing, stats: NotifierStats
    ) -> None:
        started = time.perf_counter()

        if self.dry_run:
            stats.sent += 1
            listing.notified_at = time.time()
            elapsed = (time.perf_counter() - started) * 1000
            stats.latencies.append(elapsed)
            log.info(
                "[SIMULATION] %s aurait envoyé : %s — %s ¥",
                notifier.name, listing.title[:60], listing.price,
            )
            # Même en simulation, la chaîne de mesure doit être exercée :
            # c'est ce qui garantit qu'elle marchera en vrai.
            if self.on_sent:
                self.on_sent(listing, notifier.name, True, elapsed, "")
            return

        for attempt in range(self.max_retries):
            try:
                await notifier.send(listing)
            except Exception as exc:
                retry_after = getattr(exc, "retry_after", None)
                if attempt < self.max_retries - 1:
                    stats.retries += 1
                    # Recul exponentiel, sauf si le service dit lui-même
                    # combien attendre (429 avec Retry-After).
                    delay = retry_after if retry_after else 0.5 * (2 ** attempt)
                    await asyncio.sleep(min(delay, 30.0))
                    continue
                stats.failed += 1
                stats.last_error = str(exc)[:200]
                log.warning("%s : envoi abandonné — %s", notifier.name, exc)
                if self.on_sent:
                    self.on_sent(listing, notifier.name, False, 0, str(exc))
                return

            elapsed = (time.perf_counter() - started) * 1000
            stats.sent += 1
            stats.latencies.append(elapsed)
            listing.notified_at = time.time()   # avant on_sent : il en dépend
            log.info("[%s] notification envoyée en %.0f ms", notifier.name, elapsed)
            if self.on_sent:
                self.on_sent(listing, notifier.name, True, elapsed, "")
            return

    async def drain(self, timeout: float = 5.0) -> None:
        """Attend que les files se vident. Utilisé à l'arrêt et par `--once`."""
        try:
            await asyncio.wait_for(
                asyncio.gather(*(q.join() for q in self._queues.values())),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            log.warning("notifications encore en attente après %.0f s", timeout)

    async def stop(self) -> None:
        if not self._running:
            return
        await self.drain()
        self._running = False
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        for notifier in self.notifiers:
            try:
                await notifier.close()
            except Exception:
                log.debug("fermeture %s", notifier.name, exc_info=True)

    def report(self) -> dict[str, Any]:
        return {
            name: {
                **stats.to_dict(),
                "queued": self._queues[name].qsize() if name in self._queues else 0,
            }
            for name, stats in self.stats.items()
        }
