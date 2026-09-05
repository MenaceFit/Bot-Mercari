"""Circuit breaker par source.

Sans lui, une source en panne continue d'être interrogée à pleine cadence :
elle consomme le budget de requêtes, remplit les logs, et fait chuter les
métriques de latence sans qu'aucune annonce n'en sorte. Les autres sources
en pâtissent alors qu'elles vont bien.

Trois états, la mécanique classique :

    CLOSED    tout va bien, les requêtes passent
    OPEN      trop d'échecs — on ne demande plus rien pendant `cooldown`
    HALF_OPEN une requête d'essai ; elle décide du retour en CLOSED ou OPEN

Ce qui compte pour un scanner, c'est que **OPEN rende immédiatement la
main** : une source coupée ne doit rien coûter du tout, pas même un timeout.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

log = logging.getLogger(__name__)


class State(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    name: str
    #: Échecs consécutifs avant ouverture. Trois : une erreur isolée arrive,
    #: trois d'affilée sont une panne.
    threshold: int = 3
    #: Temps d'ouverture avant le premier essai. Doublé à chaque échec en
    #: HALF_OPEN, plafonné — une source durablement morte ne doit pas être
    #: retestée toutes les trente secondes pendant des jours.
    cooldown: float = 30.0
    max_cooldown: float = 600.0

    state: State = State.CLOSED
    failures: int = 0
    opened_at: float = 0.0
    current_cooldown: float = 0.0
    trips: int = 0
    last_error: str = ""
    #: Journal des transitions, pour la page « System » du dashboard.
    history: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.current_cooldown = self.cooldown

    # ── Décision ──────────────────────────────────────────────────────────
    def allows(self, now: float | None = None) -> bool:
        """Peut-on tenter une requête ? Ne bloque jamais."""
        now = time.monotonic() if now is None else now
        if self.state is State.CLOSED:
            return True
        if self.state is State.OPEN:
            if now - self.opened_at >= self.current_cooldown:
                self._transition(State.HALF_OPEN, "fin du délai, essai")
                return True
            return False
        # HALF_OPEN : une seule requête d'essai à la fois.
        return True

    def record_success(self) -> None:
        if self.state is not State.CLOSED:
            self._transition(State.CLOSED, "source rétablie")
        self.failures = 0
        self.current_cooldown = self.cooldown

    def record_failure(self, error: str = "", now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.failures += 1
        self.last_error = error[:200]

        if self.state is State.HALF_OPEN:
            # L'essai a échoué : on rouvre, et on attend plus longtemps.
            self.current_cooldown = min(
                self.max_cooldown, self.current_cooldown * 2
            )
            self._open(now, "essai échoué")
        elif self.failures >= self.threshold:
            self._open(now, f"{self.failures} échecs consécutifs")

    def _open(self, now: float, reason: str) -> None:
        self.opened_at = now
        self.trips += 1
        self._transition(State.OPEN, reason)

    def _transition(self, state: State, reason: str) -> None:
        if state is self.state:
            return
        previous, self.state = self.state, state
        entry = {
            "at": time.time(), "from": previous.value,
            "to": state.value, "reason": reason,
        }
        self.history.append(entry)
        del self.history[:-20]

        if state is State.OPEN:
            log.error(
                "circuit OUVERT sur « %s » (%s) — plus aucune requête pendant "
                "%.0f s. Les autres sources continuent.",
                self.name, reason, self.current_cooldown,
            )
        elif state is State.CLOSED:
            log.info("circuit refermé sur « %s » — %s", self.name, reason)

    def to_dict(self) -> dict[str, Any]:
        remaining = 0.0
        if self.state is State.OPEN:
            remaining = max(
                0.0, self.current_cooldown - (time.monotonic() - self.opened_at)
            )
        return {
            "name": self.name,
            "state": self.state.value,
            "failures": self.failures,
            "trips": self.trips,
            "cooldown": round(self.current_cooldown, 1),
            "reopens_in": round(remaining, 1),
            "last_error": self.last_error,
            "history": list(self.history[-5:]),
        }
