"""Conversion de devises, avec cache.

Contrainte du cahier des charges (§10) : « NE PAS appeler une API externe
pour chaque annonce ». Un appel réseau par annonce ajouterait plus de
latence que tout le reste du pipeline réuni — pour une information qui
bouge de moins d'un pourcent par jour.

Le taux est donc :

1. lu depuis la configuration (contrôle total, zéro réseau) ;
2. ou récupéré une fois, puis mis en cache pour `ttl` secondes ;
3. et **jamais bloquant** : si l'actualisation échoue, on garde le dernier
   taux connu et on l'annonce comme périmé plutôt que d'échouer ou, pire,
   d'afficher un prix faux sans le dire.

Le taux affiché porte toujours son âge : « ≈ 76 € » sans savoir de quand
date le taux serait une information trompeuse.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

#: Taux de repli, utilisés uniquement si rien d'autre n'est disponible.
#: Ils sont forcément périmés — le système le signale explicitement plutôt
#: que de faire passer une approximation pour une conversion.
FALLBACK_RATES: dict[str, float] = {
    "EUR": 0.0061,      # 1 JPY ≈ 0,0061 EUR
    "USD": 0.0067,
    "GBP": 0.0052,
}


@dataclass
class RateCache:
    """Un taux, avec sa provenance et son âge."""

    rate: float
    source: str
    fetched_at: float = field(default_factory=time.time)

    def age(self) -> float:
        return time.time() - self.fetched_at

    def is_stale(self, ttl: float) -> bool:
        return self.age() > ttl


class CurrencyConverter:
    """Convertit depuis le JPY. Aucun appel réseau sur le chemin critique."""

    def __init__(
        self,
        *,
        base: str = "JPY",
        target: str = "EUR",
        fixed_rate: float | None = None,
        ttl: float = 21600.0,        # 6 h
        allow_network: bool = False,
    ) -> None:
        self.base = base
        self.target = target
        self.ttl = ttl
        self.allow_network = allow_network
        self._cache: RateCache | None = None

        if fixed_rate:
            # Taux imposé par la configuration : le plus prévisible, et le
            # seul qui ne dépende de rien d'extérieur.
            self._cache = RateCache(rate=fixed_rate, source="config")
        elif target in FALLBACK_RATES:
            self._cache = RateCache(
                rate=FALLBACK_RATES[target], source="fallback",
                fetched_at=0.0,      # âge infini : marqué périmé d'emblée
            )

    # ── Chemin critique : purement local ──────────────────────────────────
    def convert(self, amount: int | float) -> float:
        if not amount or self._cache is None:
            return 0.0
        return amount * self._cache.rate

    def format(self, amount: int | float) -> str:
        """« ≈ 76 € ». Le « ≈ » n'est pas décoratif : c'est une conversion."""
        converted = self.convert(amount)
        if not converted:
            return ""
        symbol = {"EUR": "€", "USD": "$", "GBP": "£"}.get(self.target, self.target)
        return f"≈ {converted:,.0f} {symbol}".replace(",", " ")

    @property
    def rate(self) -> float:
        return self._cache.rate if self._cache else 0.0

    @property
    def stale(self) -> bool:
        return self._cache is None or self._cache.is_stale(self.ttl)

    # ── Actualisation : hors chemin critique, jamais bloquante ────────────
    async def refresh(self) -> bool:
        """Tente d'actualiser le taux. Un échec n'est jamais fatal."""
        if not self.allow_network or not self.stale:
            return False
        try:
            import httpx

            url = (
                "https://api.frankfurter.app/latest"
                f"?from={self.base}&to={self.target}"
            )
            async with httpx.AsyncClient(timeout=6.0) as client:
                response = await client.get(url)
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}")
            rate = float((response.json().get("rates") or {})[self.target])
        except Exception as exc:
            # On garde le dernier taux connu. Échouer ici ferait tomber une
            # fonctionnalité d'affichage pour une raison purement externe.
            log.warning(
                "taux de change non actualisé (%s) — on garde %s (%s)",
                exc, self.rate, self._cache.source if self._cache else "aucun",
            )
            return False

        self._cache = RateCache(rate=rate, source="frankfurter.app")
        log.info("taux %s→%s actualisé : %.6f", self.base, self.target, rate)
        return True

    def status(self) -> dict[str, Any]:
        if self._cache is None:
            return {"available": False}
        return {
            "available": True,
            "base": self.base,
            "target": self.target,
            "rate": self._cache.rate,
            "source": self._cache.source,
            "age_seconds": round(self._cache.age()),
            "stale": self.stale,
        }
