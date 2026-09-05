"""Ordonnancement des scans.

Trois exigences que `while True: scan(); sleep(10)` ne satisfait pas :

1. **Pas de dérive.** L'échéance suivante se calcule depuis la précédente,
   pas depuis la fin du travail. Sinon chaque scan ajoute sa propre durée à
   l'intervalle, et une cadence « toutes les 2 s » devient 2,3 puis 2,7 s.

2. **Des priorités.** Un mot-clé rare mérite d'être revisité toutes les 2 s ;
   une veille de fond peut se contenter de 30 s. Les deux ne doivent pas
   partager la même cadence.

3. **Un budget partagé, réparti explicitement.** Le débit total est limité
   (rate limits, politesse). Laisser chaque tâche viser son intervalle idéal
   et se bloquer sur un token bucket fait accumuler du retard EN SILENCE :
   les scans dérivent, des annonces passent entre deux passages, et rien ne
   le signale. On préfère répartir d'avance et afficher la cadence réellement
   tenue.

Le jitter désynchronise les tâches : sans lui, toutes les requêtes partent
au même instant, créant des pics qui déclenchent des 429 alors que le débit
moyen est correct.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any

#: Cadences par défaut, en secondes. Configurables dans le YAML.
DEFAULT_INTERVALS = {"high": 2.0, "medium": 5.0, "low": 20.0}
#: Poids relatifs pour la répartition du budget quand il ne suffit pas.
#: Une tâche « high » reçoit dix fois la part d'une tâche « low ».
WEIGHTS = {"high": 10.0, "medium": 3.0, "low": 1.0}


@dataclass
class ScheduledTask:
    """Une requête à répéter, avec sa cadence propre."""

    key: str                      # identifie la tâche (source + requête)
    source: str
    query: str
    priority: str = "medium"
    interval: float = 5.0
    #: Cadence demandée, avant répartition du budget. Garder les deux permet
    #: de dire « tu voulais 2 s, tu as 6,4 s » au lieu de le taire.
    target_interval: float = 5.0
    deadline: float = 0.0
    runs: int = 0
    paused: bool = False

    def due(self, now: float) -> bool:
        return not self.paused and now >= self.deadline

    def schedule_next(self, now: float, jitter: float = 0.15) -> None:
        factor = 1.0 + random.uniform(-jitter, jitter)
        step = max(0.05, self.interval * factor)
        self.deadline += step
        # Retard accumulé : on repart de maintenant plutôt que d'enchaîner
        # sans pause pour rattraper une dette qui ne se rattrapera pas.
        if self.deadline < now:
            self.deadline = now + step
        self.runs += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "source": self.source,
            "query": self.query,
            "priority": self.priority,
            "interval": round(self.interval, 2),
            "target_interval": round(self.target_interval, 2),
            "throttled": self.interval > self.target_interval * 1.05,
            "runs": self.runs,
            "paused": self.paused,
        }


@dataclass
class Scheduler:
    """Décide quelle requête part, et quand."""

    intervals: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_INTERVALS)
    )
    budget_per_second: float = 8.0
    jitter: float = 0.15
    tasks: dict[str, ScheduledTask] = field(default_factory=dict)

    def sync(self, wanted: list[tuple[str, str, str]]) -> tuple[int, int]:
        """Aligne les tâches sur le plan courant.

        `wanted` : (clé, source, requête, priorité) aplati en triplets
        (source, requête, priorité). Renvoie (ajoutées, retirées).

        Les tâches existantes gardent leur échéance : reconstruire le plan
        ne doit pas remettre tous les compteurs à zéro et provoquer une
        rafale de requêtes simultanées.
        """
        keys = set()
        added = 0
        now = time.monotonic()

        for index, (source, query, priority) in enumerate(wanted):
            key = f"{source}::{query}"
            keys.add(key)
            existing = self.tasks.get(key)
            target = self.intervals.get(priority, DEFAULT_INTERVALS["medium"])
            if existing is None:
                self.tasks[key] = ScheduledTask(
                    key=key,
                    source=source,
                    query=query,
                    priority=priority,
                    interval=target,
                    target_interval=target,
                    # Étalement initial : sans lui, toutes les tâches partent
                    # au même instant au démarrage.
                    deadline=now + (index / max(1, len(wanted))) * target,
                )
                added += 1
            else:
                existing.priority = priority
                existing.target_interval = target

        removed = 0
        for key in list(self.tasks):
            if key not in keys:
                del self.tasks[key]
                removed += 1

        self.allocate()
        return added, removed

    def allocate(self) -> dict[str, Any]:
        """Répartit le budget de requêtes entre les tâches actives.

        Chaque tâche reçoit une part proportionnelle au poids de sa priorité.
        Si sa part permet la cadence visée, elle la garde ; sinon son
        intervalle est allongé jusqu'à ce que la somme tienne dans le budget.
        """
        active = [t for t in self.tasks.values() if not t.paused]
        if not active or self.budget_per_second <= 0:
            return {"throttled": 0, "demand": 0.0, "budget": self.budget_per_second}

        total_weight = sum(WEIGHTS.get(t.priority, 1.0) for t in active)
        throttled = 0
        for task in active:
            share = self.budget_per_second * WEIGHTS.get(task.priority, 1.0) / total_weight
            floor = 1.0 / share if share > 0 else task.target_interval
            if floor > task.target_interval:
                task.interval = floor
                throttled += 1
            else:
                task.interval = task.target_interval

        wanted = sum(1.0 / t.target_interval for t in active if t.target_interval > 0)
        return {
            "throttled": throttled,
            "demand": round(wanted, 2),
            "budget": self.budget_per_second,
            "saturated": wanted > self.budget_per_second * 1.02,
            "tasks": len(active),
        }

    def due(self, now: float | None = None) -> list[ScheduledTask]:
        """Tâches arrivées à échéance, les plus prioritaires d'abord."""
        now = time.monotonic() if now is None else now
        ready = [task for task in self.tasks.values() if task.due(now)]
        order = {"high": 0, "medium": 1, "low": 2}
        ready.sort(key=lambda t: (order.get(t.priority, 1), t.deadline))
        return ready

    def next_deadline(self, now: float | None = None) -> float:
        """Délai avant la prochaine échéance, borné pour rester réactif."""
        now = time.monotonic() if now is None else now
        active = [t.deadline for t in self.tasks.values() if not t.paused]
        if not active:
            return 0.5
        return max(0.0, min(min(active) - now, 1.0))

    def report(self) -> dict[str, Any]:
        allocation = self.allocate()
        return {
            **allocation,
            "by_priority": {
                priority: sum(
                    1 for t in self.tasks.values() if t.priority == priority
                )
                for priority in ("high", "medium", "low")
            },
            "effective_interval": round(
                len([t for t in self.tasks.values() if not t.paused])
                / self.budget_per_second, 2
            ) if self.budget_per_second else 0.0,
        }
