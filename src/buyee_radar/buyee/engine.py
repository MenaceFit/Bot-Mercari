"""`BuyeeSearchEngine` — la couche plateforme.

MERCARI IS A SOURCE. BUyee IS THE TARGET SEARCH ECOSYSTEM.

Le moteur reçoit UNE requête et l'envoie à TOUTES les sources activées,
**en parallèle**. Il ne connaît aucune marketplace en particulier : il
parcourt le registre. Ajouter une source, c'est ajouter une entrée au
registre — pas une branche `if` ici.

Ce que le moteur garantit
-------------------------
1. **Parallélisme réel.** `asyncio.gather` sur toutes les sources. Jamais
   « Mercari d'abord, puis Rakuma ».
2. **Comptes honnêtes.** Le rapport dit quelles sources ont été
   *réellement interrogées*, lesquelles ont été écartées et pourquoi. Le
   nombre affiché par le dashboard vient de là — il ne peut pas diverger.
3. **Isolation.** Une source qui échoue, qui expire ou qui lève renvoie un
   `SearchResult(ok=False)` ; les autres continuent. Le moteur ne propage
   jamais l'exception d'une source.
4. **Attribution.** Un résultat de cross-search est rendu à sa vraie
   marketplace avant de sortir d'ici.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from ..adapters.base import Listing, SearchQuery, SearchResult, SupportLevel
from .adapters import ADAPTERS, BuyeeSourceAdapter
from .registry import SOURCES, DISPLAY_ORDER, BuyeeSource, resolve

log = logging.getLogger(__name__)


@dataclass(slots=True)
class SourceOutcome:
    """Ce qu'une source a donné pour une requête. Y compris rien, et pourquoi."""

    source: str
    label: str
    queried: bool
    ok: bool = False
    count: int = 0
    latency_ms: int = 0
    error: str = ""
    skipped_reason: str = ""
    support: str = ""

    def to_dict(self) -> dict:
        return {
            "source": self.source, "label": self.label,
            "queried": self.queried, "ok": self.ok, "count": self.count,
            "latency_ms": self.latency_ms, "error": self.error,
            "skipped_reason": self.skipped_reason, "support": self.support,
        }


@dataclass(slots=True)
class BuyeeSearchReport:
    """Le résultat d'une recherche Buyee, source par source."""

    query: str
    listings: list[Listing] = field(default_factory=list)
    outcomes: list[SourceOutcome] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def elapsed_ms(self) -> int:
        if not (self.started_at and self.finished_at):
            return 0
        return round((self.finished_at - self.started_at) * 1000)

    @property
    def queried(self) -> list[SourceOutcome]:
        return [o for o in self.outcomes if o.queried]

    @property
    def skipped(self) -> list[SourceOutcome]:
        return [o for o in self.outcomes if not o.queried]

    @property
    def ok_count(self) -> int:
        return sum(1 for o in self.outcomes if o.ok)

    @property
    def per_source(self) -> dict[str, int]:
        """Combien d'annonces par marketplace, APRÈS attribution."""
        counts: dict[str, int] = {}
        for listing in self.listings:
            counts[listing.source] = counts.get(listing.source, 0) + 1
        return counts

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "total": len(self.listings),
            "elapsed_ms": self.elapsed_ms,
            "sources_queried": len(self.queried),
            "sources_ok": self.ok_count,
            "per_source": self.per_source,
            "outcomes": [o.to_dict() for o in self.outcomes],
        }


class SourceRegistry:
    """Les sources Buyee instanciées, et leur état d'activation.

    C'est la table que le cahier des charges décrit :

        sources = {
            "mercari": MercariAdapter(),
            "rakuma": RakumaAdapter(),
            "jdirectitems_auction": JDirectItemsAuctionAdapter(),
            ...
        }

    à ceci près qu'une source non supportée n'est pas absente : elle est
    présente, désactivée, avec son motif. Le dashboard doit pouvoir dire
    « ZOZOTOWN — UNSUPPORTED — pas de flux de nouveautés » plutôt que de
    faire comme si la source n'existait pas.
    """

    def __init__(self, adapters: dict[str, BuyeeSourceAdapter] | None = None) -> None:
        self._adapters: dict[str, BuyeeSourceAdapter] = dict(adapters or {})
        self._enabled: set[str] = set(self._adapters)

    # ── Construction ──────────────────────────────────────────────────────
    @classmethod
    def build(
        cls,
        specs: dict[str, dict] | None = None,
        *,
        include_unsupported: bool = False,
    ) -> "SourceRegistry":
        """Instancie les adapters demandés.

        `specs` : identifiant → options (`enabled`, `selectors`, timeouts…).
        Une source non supportée n'est instanciée que si on le demande
        explicitement — le dashboard le fait, pour pouvoir l'afficher.
        """
        from .adapters import build as build_adapter
        from ..adapters.buyee_html import Selectors

        registry = cls()
        specs = specs or {}
        known = set(Selectors.__dataclass_fields__)

        for source_id in DISPLAY_ORDER:
            spec = SOURCES[source_id]
            options = dict(specs.get(source_id) or specs.get(_legacy(source_id)) or {})
            enabled = bool(options.pop("enabled", False))

            if spec.support is SupportLevel.UNSUPPORTED:
                if enabled:
                    log.warning(
                        "source « %s » activée mais NON SUPPORTÉE — %s",
                        source_id, spec.support_note,
                    )
                if not include_unsupported:
                    continue
                enabled = False

            selectors = Selectors(**{
                k: v for k, v in (options.pop("selectors", None) or {}).items()
                if k in known
            })
            options.pop("page_size", None)
            try:
                adapter = build_adapter(source_id, selectors=selectors, **options)
            except (TypeError, ValueError) as exc:
                log.error("source « %s » ignorée : %s", source_id, exc)
                continue

            registry.add(adapter, enabled=enabled)
        return registry

    def add(self, adapter: BuyeeSourceAdapter, *, enabled: bool = True) -> None:
        self._adapters[adapter.source] = adapter
        if enabled:
            self._enabled.add(adapter.source)
        else:
            self._enabled.discard(adapter.source)

    # ── Consultation ──────────────────────────────────────────────────────
    def __contains__(self, source_id: object) -> bool:
        return resolve(str(source_id)) in self._adapters

    def __len__(self) -> int:
        return len(self._adapters)

    def get(self, source_id: str) -> BuyeeSourceAdapter | None:
        return self._adapters.get(resolve(source_id))

    @property
    def all(self) -> list[BuyeeSourceAdapter]:
        """Toutes les sources, dans l'ordre d'affichage du registre."""
        order = {name: i for i, name in enumerate(DISPLAY_ORDER)}
        return sorted(self._adapters.values(), key=lambda a: order.get(a.source, 99))

    @property
    def enabled(self) -> list[BuyeeSourceAdapter]:
        return [a for a in self.all if a.source in self._enabled]

    @property
    def usable(self) -> list[BuyeeSourceAdapter]:
        """Activées ET réellement interrogeables maintenant."""
        def ready(adapter) -> bool:
            if adapter.support is SupportLevel.UNSUPPORTED:
                return False
            if not isinstance(adapter, BuyeeSourceAdapter):
                return True   # simulateur : prêt par construction
            return bool(adapter.search_url and adapter.selectors.calibrated)

        return [a for a in self.enabled if ready(a)]

    def enable(self, source_id: str, on: bool = True) -> bool:
        canonical = resolve(source_id)
        if canonical not in self._adapters:
            return False
        spec = SOURCES[canonical]
        if on and spec.support is SupportLevel.UNSUPPORTED:
            return False
        self._enabled.add(canonical) if on else self._enabled.discard(canonical)
        return True

    def spec(self, source_id: str) -> BuyeeSource | None:
        return SOURCES.get(resolve(source_id))

    def counts(self) -> dict[str, int]:
        """« Sources 5/8 » du dashboard — calculé, pas écrit en dur."""
        return {
            "total": len(SOURCES),
            "registered": len(self._adapters),
            "enabled": len(self.enabled),
            "usable": len(self.usable),
        }

    # ── Cycle de vie ──────────────────────────────────────────────────────
    async def start(self) -> None:
        await asyncio.gather(
            *(a.start() for a in self.enabled), return_exceptions=True
        )

    async def stop(self) -> None:
        await asyncio.gather(
            *(a.stop() for a in self.all), return_exceptions=True
        )


class BuyeeSearchEngine:
    """Une requête → toutes les sources Buyee activées, en parallèle."""

    def __init__(
        self,
        registry: SourceRegistry,
        *,
        affiliate_id: str = "",
        max_concurrency: int = 20,
    ) -> None:
        self.registry = registry
        self.affiliate_id = affiliate_id
        self._gate = asyncio.Semaphore(max(1, max_concurrency))

    async def search(
        self,
        text: str,
        *,
        sources: list[str] | None = None,
        limit: int = 60,
        min_price: int | None = None,
        max_price: int | None = None,
    ) -> BuyeeSearchReport:
        """Interroge les sources demandées — toutes celles activées par défaut.

        Ne lève jamais. Une source en échec apparaît dans le rapport avec
        son motif ; les autres ont quand même répondu.
        """
        query = SearchQuery(
            text=text, limit=limit, min_price=min_price, max_price=max_price
        )
        report = BuyeeSearchReport(query=text, started_at=time.time())

        wanted = {resolve(s) for s in sources} if sources else None
        targets: list[BuyeeSourceAdapter] = []

        for adapter in self.registry.all:
            spec = self.registry.spec(adapter.source)
            label = spec.label if spec else adapter.source
            reason = self._skip_reason(adapter, wanted)
            if reason:
                report.outcomes.append(SourceOutcome(
                    source=adapter.source, label=label, queried=False,
                    skipped_reason=reason, support=adapter.support.value,
                ))
                continue
            targets.append(adapter)

        if targets:
            # LE point du cahier des charges : toutes les sources partent
            # ensemble. Pas de boucle séquentielle, pas de source
            # privilégiée.
            results = await asyncio.gather(
                *(self._one(a, query) for a in targets),
                return_exceptions=True,
            )
            for adapter, result in zip(targets, results):
                spec = self.registry.spec(adapter.source)
                label = spec.label if spec else adapter.source
                if isinstance(result, BaseException):
                    log.warning(
                        "source « %s » a levé : %s", adapter.source, result
                    )
                    report.outcomes.append(SourceOutcome(
                        source=adapter.source, label=label, queried=True,
                        ok=False, error=str(result)[:200],
                        support=adapter.support.value,
                    ))
                    continue
                enrich = getattr(adapter, "enrich", None)
                if enrich is not None:
                    for listing in result.listings:
                        enrich(listing, self.affiliate_id)
                report.listings.extend(result.listings)
                report.outcomes.append(SourceOutcome(
                    source=adapter.source, label=label, queried=True,
                    ok=result.ok, count=len(result.listings),
                    latency_ms=result.latency_ms, error=result.error[:200],
                    support=adapter.support.value,
                ))

        report.finished_at = time.time()
        return report

    async def _one(
        self, adapter: BuyeeSourceAdapter, query: SearchQuery
    ) -> SearchResult:
        async with self._gate:
            return await adapter.search(query)

    def _skip_reason(
        self, adapter: BuyeeSourceAdapter, wanted: set[str] | None
    ) -> str:
        # Ordre choisi : on donne toujours la raison la plus PROFONDE.
        # Dire « désactivée » d'une source qui de toute façon ne peut pas
        # être interrogée enverrait l'utilisateur cocher une case qui ne
        # changerait rien.
        if adapter.support is SupportLevel.UNSUPPORTED:
            return f"non supportée — {adapter.support_note[:120]}"
        if wanted is not None and adapter.source not in wanted:
            return "non demandée"
        if adapter.source not in {a.source for a in self.registry.enabled}:
            return "désactivée dans radar.yaml"
        # Les deux contrôles suivants ne valent que pour un adapter Buyee.
        # Un simulateur n'a ni URL ni sélecteurs, et c'est normal : il ne
        # prétend pas être une source réelle, son nom commence par « sim_ ».
        if not isinstance(adapter, BuyeeSourceAdapter):
            return ""
        if not adapter.search_url:
            return "aucune URL de recherche connue pour cette source"
        if not adapter.selectors.calibrated:
            return "sélecteurs non calibrés (buyee-radar calibrate)"
        return ""


def _legacy(source_id: str) -> str:
    """Ancien identifiant correspondant, pour lire une config existante."""
    from .registry import ALIASES

    for old, new in ALIASES.items():
        if new == source_id:
            return old
    return source_id
