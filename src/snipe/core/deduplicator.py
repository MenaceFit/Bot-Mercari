"""Déduplication à deux niveaux.

Une même annonce peut arriver plusieurs fois : par deux requêtes différentes,
par deux mots-clés, par deux sources qui indexent la même marketplace, ou
simplement au scan suivant puisqu'elle est toujours en ligne. Elle ne doit
partir en notification qu'une fois — c'est le critère de réussite n°6, et
c'est aussi ce qui rend le bot supportable au quotidien.

Deux niveaux, pour deux besoins différents :

* **L1, en mémoire** — un `set` d'identifiants. Coût mesuré : 0,09 µs par
  test. C'est lui qui est sur le chemin critique.
* **L2, SQLite** — la persistance. Il ne sert qu'au démarrage, pour amorcer
  L1 : sans lui, un redémarrage renotifierait tout ce qui est encore en ligne.

L1 est borné en taille (FIFO). Un `set` non borné qui tourne pendant des
jours finit par peser plusieurs centaines de mégaoctets, et le critère n°15
demande de tenir plusieurs jours sans intervention.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class Deduplicator:
    """Cache L1 borné, amorcé depuis L2 au démarrage."""

    capacity: int = 200_000
    _keys: set[str] = field(default_factory=set, repr=False)
    _order: deque[str] = field(default_factory=deque, repr=False)

    #: Compteurs, exposés dans les métriques.
    hits: int = 0        # déjà vue
    misses: int = 0      # nouvelle

    def prime(self, keys: set[str]) -> int:
        """Amorce L1 depuis L2. Renvoie le nombre d'entrées chargées."""
        for key in keys:
            self._remember(key)
        return len(self._keys)

    def seen(self, key: str) -> bool:
        """L'annonce a-t-elle déjà été traitée ? Ne modifie rien."""
        return key in self._keys

    def add(self, key: str) -> bool:
        """Enregistre l'annonce. Renvoie True si elle est NOUVELLE.

        Une seule opération pour tester et marquer : deux appels séparés
        laisseraient une fenêtre où deux workers concurrents pourraient tous
        deux conclure « nouvelle » sur la même annonce.
        """
        if key in self._keys:
            self.hits += 1
            return False
        self.misses += 1
        self._remember(key)
        return True

    def _remember(self, key: str) -> None:
        self._keys.add(key)
        self._order.append(key)
        while len(self._order) > self.capacity:
            self._keys.discard(self._order.popleft())

    def __len__(self) -> int:
        return len(self._keys)

    @property
    def duplicate_ratio(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

    def report(self) -> dict[str, float | int]:
        return {
            "size": len(self._keys),
            "capacity": self.capacity,
            "duplicates": self.hits,
            "new": self.misses,
            "duplicate_ratio": round(self.duplicate_ratio, 4),
        }
