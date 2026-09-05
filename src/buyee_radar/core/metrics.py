"""Métriques : latences par étape, compteurs, santé des sources.

Le cahier des charges est explicite : « NE FAIS PAS DE FAUSSES PROMESSES SUR
LA LATENCE ». Ce module en tire deux conséquences concrètes.

1. **Une latence dont un bout est inconnu n'est pas enregistrée.** La plupart
   des marketplaces ne disent pas quand elles ont *indexé* une annonce (T1),
   seulement quand le vendeur l'a publiée (T0). Un T1 deviné ferait baisser
   artificiellement les chiffres.
2. **Les percentiles sont calculés sur une fenêtre glissante**, pas depuis le
   démarrage. Une moyenne sur trois jours ne dit rien de l'état actuel.

Les percentiles utilisent la méthode du plus proche rang sur un échantillon
borné : exact sur la fenêtre observée, coût constant en mémoire.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

#: Fenêtre d'échantillonnage des latences. 2000 mesures couvrent largement
#: la dernière heure à la cadence nominale, sans grossir indéfiniment.
WINDOW = 2000


@dataclass
class Histogram:
    """Échantillon borné, avec percentiles exacts sur la fenêtre."""

    values: deque[float] = field(default_factory=lambda: deque(maxlen=WINDOW))

    def add(self, value: float) -> None:
        # Une valeur nulle signifie « inconnu » partout dans le système :
        # l'enregistrer tirerait les percentiles vers le bas et donnerait
        # une fausse impression de rapidité.
        if value > 0:
            self.values.append(value)

    def percentile(self, p: float) -> float:
        if not self.values:
            return 0.0
        ordered = sorted(self.values)
        index = min(len(ordered) - 1, max(0, int(round(p * len(ordered))) - 1))
        return ordered[index]

    @property
    def avg(self) -> float:
        return sum(self.values) / len(self.values) if self.values else 0.0

    def summary(self) -> dict[str, float]:
        return {
            "count": len(self.values),
            "avg": round(self.avg, 1),
            "p50": round(self.percentile(0.50), 1),
            "p95": round(self.percentile(0.95), 1),
            "p99": round(self.percentile(0.99), 1),
        }


@dataclass
class Counter:
    """Compteur avec débit par minute sur fenêtre glissante."""

    total: int = 0
    _events: deque[float] = field(default_factory=lambda: deque(maxlen=10_000))

    def inc(self, n: int = 1) -> None:
        self.total += n
        now = time.time()
        for _ in range(min(n, 100)):
            self._events.append(now)

    def per_minute(self, window: float = 60.0) -> float:
        cutoff = time.time() - window
        recent = sum(1 for ts in self._events if ts >= cutoff)
        return round(recent * 60.0 / window, 1)


@dataclass
class SourceStats:
    """Santé et débit d'une source, indépendamment des autres."""

    name: str
    verified: bool = True
    requests: Counter = field(default_factory=Counter)
    errors: Counter = field(default_factory=Counter)
    rate_limits: Counter = field(default_factory=Counter)
    items: Counter = field(default_factory=Counter)
    latency: Histogram = field(default_factory=Histogram)

    consecutive_errors: int = 0
    last_ok: float = 0.0
    last_error: str = ""
    #: `None` = jamais testée. Distinct de `False`, qui veut dire « testée et
    #: en échec » — ne pas confondre les deux évite d'annoncer une panne
    #: avant le premier health check.
    healthy: bool | None = None

    def record_ok(self, latency_ms: float, items: int) -> None:
        self.requests.inc()
        self.items.inc(items)
        self.latency.add(latency_ms)
        self.consecutive_errors = 0
        self.last_ok = time.time()
        self.healthy = True

    def record_error(self, message: str, rate_limited: bool = False) -> None:
        self.requests.inc()
        self.errors.inc()
        if rate_limited:
            self.rate_limits.inc()
        self.consecutive_errors += 1
        self.last_error = message[:200]
        # Une erreur isolée arrive ; trois d'affilée, c'est une panne.
        if self.consecutive_errors >= 3:
            self.healthy = False

    @property
    def availability(self) -> float:
        total = self.requests.total
        return (total - self.errors.total) / total if total else 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "verified": self.verified,
            "healthy": self.healthy,
            "requests": self.requests.total,
            "requests_per_min": self.requests.per_minute(),
            "errors": self.errors.total,
            "rate_limits": self.rate_limits.total,
            "items": self.items.total,
            "availability": round(self.availability, 4),
            "latency_ms": self.latency.summary(),
            "last_ok": self.last_ok,
            "last_error": self.last_error,
        }


@dataclass
class Metrics:
    """Agrégat global, prêt à afficher ou à servir au dashboard."""

    started_at: float = field(default_factory=time.time)
    sources: dict[str, SourceStats] = field(default_factory=dict)

    new_listings: Counter = field(default_factory=Counter)
    duplicates: Counter = field(default_factory=Counter)
    notifications: Counter = field(default_factory=Counter)
    notification_failures: Counter = field(default_factory=Counter)

    #: Les étapes du cahier des charges, mesurées séparément.
    network: Histogram = field(default_factory=Histogram)      # T2 → T3
    detection: Histogram = field(default_factory=Histogram)    # T0 → T3
    pipeline: Histogram = field(default_factory=Histogram)     # T3 → T4
    notify: Histogram = field(default_factory=Histogram)       # T4 → T5
    total: Histogram = field(default_factory=Histogram)        # T0 → T5

    def source(self, name: str, verified: bool = True) -> SourceStats:
        stats = self.sources.get(name)
        if stats is None:
            stats = SourceStats(name=name, verified=verified)
            self.sources[name] = stats
        return stats

    @property
    def uptime(self) -> float:
        return time.time() - self.started_at

    def snapshot(self) -> dict[str, Any]:
        return {
            "uptime_seconds": round(self.uptime),
            "sources": [s.to_dict() for s in self.sources.values()],
            "requests_per_min": round(
                sum(s.requests.per_minute() for s in self.sources.values()), 1
            ),
            "new_listings": self.new_listings.total,
            "new_per_min": self.new_listings.per_minute(),
            "duplicates": self.duplicates.total,
            "duplicates_per_min": self.duplicates.per_minute(),
            "notifications": self.notifications.total,
            "notifications_per_min": self.notifications.per_minute(),
            "notification_failures": self.notification_failures.total,
            "latency": {
                "network_ms": self.network.summary(),
                "detection_ms": self.detection.summary(),
                "pipeline_ms": self.pipeline.summary(),
                "notify_ms": self.notify.summary(),
                "total_ms": self.total.summary(),
            },
        }

    def render(self) -> str:
        """Le bloc d'état périodique, tel que demandé dans le cahier."""
        snap = self.snapshot()
        lines = [
            "=" * 58,
            "SCAN STATUS",
            "",
            f"Sources actives   : {len(self.sources)}",
            f"Requêtes/min      : {snap['requests_per_min']}",
            "",
            "Latence réseau (T2→T3)     "
            f"moy {snap['latency']['network_ms']['avg']:.0f} ms  "
            f"p95 {snap['latency']['network_ms']['p95']:.0f} ms",
            "Détection (T0→T3)          "
            f"moy {snap['latency']['detection_ms']['avg']/1000:.1f} s   "
            f"p95 {snap['latency']['detection_ms']['p95']/1000:.1f} s",
            "Notification (T4→T5)       "
            f"moy {snap['latency']['notify_ms']['avg']:.0f} ms  "
            f"p95 {snap['latency']['notify_ms']['p95']:.0f} ms",
            "",
            f"Nouvelles annonces : {snap['new_listings']} "
            f"({snap['new_per_min']}/min)",
            f"Doublons écartés   : {snap['duplicates']}",
            f"Notifications      : {snap['notifications']} envoyées, "
            f"{snap['notification_failures']} en échec",
        ]
        # Largeur calculée, pas devinée : « sim_jdi_fleamarket » fait
        # 18 caractères et collait à la colonne suivante avec une largeur fixe.
        width = max((len(s.name) for s in self.sources.values()), default=0)
        width = max(16, width)
        for stats in self.sources.values():
            flag = "" if stats.verified else "  [NON VÉRIFIÉE EN RÉEL]"
            health = {True: "OK", False: "PANNE", None: "jamais testée"}[stats.healthy]
            lines.append(
                f"  {stats.name:{width}} {health:14} "
                f"{stats.requests.total:5} req  "
                f"p95 {stats.latency.percentile(0.95):.0f} ms{flag}"
            )
        lines.append("=" * 58)
        return "\n".join(lines)
