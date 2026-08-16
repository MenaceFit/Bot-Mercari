"""Limiteur de débit global (token bucket) partagé par toutes les sources.

Sans ça, ajouter un keyword multiplie mécaniquement le débit sortant et
finit en 429 / blocage IP. Ici le budget est fixé une fois pour toutes :
ajouter des sources répartit le budget au lieu de l'augmenter.
"""

from __future__ import annotations

import asyncio
import time


class TokenBucket:
    """Token bucket asynchrone, équitable (FIFO via le lock)."""

    __slots__ = ("_rate", "_capacity", "_tokens", "_updated", "_lock")

    def __init__(self, rate: float, capacity: float | None = None) -> None:
        if rate <= 0:
            raise ValueError("le débit doit être > 0")
        self._rate = rate
        self._capacity = capacity if capacity is not None else max(1.0, rate)
        self._tokens = self._capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    @property
    def rate(self) -> float:
        return self._rate

    def set_rate(self, rate: float) -> None:
        """Ajuste le débit à chaud (utilisé par le backoff adaptatif)."""
        if rate <= 0:
            raise ValueError("le débit doit être > 0")
        self._rate = rate
        self._capacity = max(self._capacity, rate)

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._updated
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._updated = now

    async def acquire(self, tokens: float = 1.0) -> float:
        """Attend qu'un jeton soit disponible. Renvoie le temps attendu (s)."""
        waited = 0.0
        # Le lock sérialise les prétendants : pas de famine ni de réveil groupé.
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return waited
                deficit = tokens - self._tokens
                delay = deficit / self._rate
                waited += delay
                await asyncio.sleep(delay)
