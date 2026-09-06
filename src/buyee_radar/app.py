"""Assemblage : de la configuration à un scanner prêt à tourner.

Seul endroit qui connaît toutes les pièces à la fois. Le scanner, lui, ne
connaît que des interfaces — c'est ce qui permet de le tester sans réseau,
sans base et sans Telegram.
"""

from __future__ import annotations

import logging
from typing import Any

from .adapters.base import MarketplaceAdapter
from .adapters.simulator import PROFILES, SimulatorAdapter
from .buyee.engine import BuyeeSearchEngine, SourceRegistry
from .buyee.registry import SOURCES, resolve
from .config.loader import Settings
from .core.currency import CurrencyConverter
from .core.deduplicator import Deduplicator
from .core.event_bus import EventBus
from .core.keywords import Keyword
from .core.matcher import FilterEngine, GlobalFilters
from .core.metrics import Metrics
from .core.scanner import Scanner
from .core.scheduler import Scheduler
from .core.scoring import ScoringConfig, ScoringEngine
from .notifications.console import ConsoleNotifier
from .notifications.discord import DiscordNotifier
from .notifications.dispatcher import NotificationHub
from .notifications.telegram import TelegramNotifier

log = logging.getLogger(__name__)

#: Surcharges de gabarit d'achat, si Buyee change de forme d'URL. Les
#: valeurs par défaut vivent dans le registre (`buyee/registry.py`), avec
#: les URL indexées qui les attestent.
BUY_TEMPLATES: dict[str, str] = {}


def build_keywords(settings: Settings) -> list[Keyword]:
    return [
        Keyword.build(
            spec.name,
            search=spec.search,
            include=spec.include,
            exclude=spec.exclude,
            include_regex=spec.include_regex,
            exclude_regex=spec.exclude_regex,
            priority=spec.priority,
            min_price=spec.min_price,
            max_price=spec.max_price,
            sources=spec.sources,
            enabled=spec.enabled,
        )
        for spec in settings.keywords
    ]


def build_registry(
    settings: Settings, *, include_unsupported: bool = True
) -> SourceRegistry:
    """Le registre des sources Buyee, tel que radar.yaml le décrit.

    Les sources non supportées y figurent quand même, désactivées : le
    dashboard doit pouvoir afficher « ZOZOTOWN — UNSUPPORTED — <motif> »
    plutôt que de faire comme si la source n'existait pas.
    """
    specs = {
        resolve(name): {
            "enabled": spec.enabled,
            "selectors": dict(spec.selectors or {}),
            "search_url": spec.search_url,
            "extra_params": dict(spec.extra_params or {}),
            "max_concurrent": spec.max_concurrent,
            "connect_timeout": spec.connect_timeout,
            "read_timeout": spec.read_timeout,
            "total_timeout": spec.total_timeout,
            "max_retries": spec.max_retries,
        }
        for name, spec in settings.sources.items()
        if not name.startswith("sim_")
    }
    unknown = set(specs) - set(SOURCES)
    for name in sorted(unknown):
        log.error(
            "source inconnue dans radar.yaml : « %s » — connues : %s",
            name, ", ".join(SOURCES),
        )
        specs.pop(name, None)
    return SourceRegistry.build(specs, include_unsupported=include_unsupported)


def build_engine(
    settings: Settings,
    registry: SourceRegistry,
    adapters: dict[str, MarketplaceAdapter] | None = None,
) -> BuyeeSearchEngine:
    """La couche plateforme : une requête → toutes les sources, en parallèle.

    Les simulateurs entrent dans le MÊME registre : sans cela, la page
    « Buyee Search » du dashboard resterait vide en mode démo, alors que le
    flux, lui, défile. Deux comptes qui divergent, c'est exactement ce que
    le cahier des charges interdit.
    """
    for name, adapter in (adapters or {}).items():
        if name.startswith("sim_") and name not in registry:
            registry.add(adapter, enabled=True)
    return BuyeeSearchEngine(
        registry,
        affiliate_id=settings.buyee.affiliate_id if settings.buyee.enabled else "",
        max_concurrency=settings.scanner.max_concurrency,
    )


def build_adapters(
    settings: Settings,
    *,
    demo: bool = False,
    registry: SourceRegistry | None = None,
) -> dict[str, MarketplaceAdapter]:
    """Les adapters que le scanner va effectivement interroger.

    Ce sont exactement les sources actives du registre — le dashboard
    compte les mêmes. Un écart entre « sources affichées » et « sources
    interrogées » serait un mensonge, et le cahier des charges le classe
    comme un défaut bloquant.
    """
    if demo:
        return {
            name: SimulatorAdapter(name, seed=1234 + index)
            for index, name in enumerate(PROFILES)
        }

    adapters: dict[str, MarketplaceAdapter] = {
        name: SimulatorAdapter(name)
        for name, spec in settings.sources.items()
        if name.startswith("sim_") and spec.enabled
    }
    registry = registry if registry is not None else build_registry(settings)
    for adapter in registry.enabled:
        adapters[adapter.source] = adapter

    if not adapters:
        log.warning(
            "aucune source active — active une source Buyee dans radar.yaml, "
            "ou lance avec --demo pour utiliser les sources simulées"
        )
    return adapters


def build_hub(
    settings: Settings,
    *,
    dry_run: bool = False,
    metrics: Metrics | None = None,
    on_sent=None,
) -> NotificationHub:
    notifiers: list[Any] = []
    n = settings.notifications

    if n.console:
        notifiers.append(ConsoleNotifier())
    if n.telegram_enabled:
        notifiers.append(
            TelegramNotifier(
                settings.telegram_token, settings.telegram_chat_id,
                enabled=True, send_photo=n.telegram_photo, silent=n.telegram_silent,
            )
        )
    if n.discord_enabled:
        notifiers.append(DiscordNotifier(settings.discord_webhook, enabled=True))

    def record(listing, channel, ok, latency_ms, error):
        if metrics is not None:
            if ok:
                metrics.notifications.inc()
                metrics.notify.add(listing.notify_ms or latency_ms)
                if listing.end_to_end_ms:
                    metrics.total.add(listing.end_to_end_ms)
            else:
                metrics.notification_failures.inc()
        if on_sent is not None:
            on_sent(listing, channel, ok, latency_ms, error)

    return NotificationHub(
        notifiers, max_queue=n.max_queue, max_retries=n.max_retries,
        dry_run=dry_run, on_sent=record,
    )


def build_scanner(
    settings: Settings,
    adapters: dict[str, MarketplaceAdapter],
    database,
    hub: NotificationHub,
    bus: EventBus,
    metrics: Metrics | None = None,
) -> Scanner:
    keywords = build_keywords(settings)
    s = settings.scanner
    f = settings.filters

    def buy_url_builder(listing):
        """Le lien Buyee. C'est le bouton principal de l'interface :
        l'utilisateur achète PAR Buyee, pas sur la marketplace d'origine."""
        if not settings.buyee.enabled:
            return ""
        source = SOURCES.get(resolve(listing.source))
        override = {**BUY_TEMPLATES, **settings.buyee.templates}.get(listing.source)
        if override and listing.listing_id:
            try:
                url = override.format(id=listing.listing_id)
            except (KeyError, IndexError, ValueError):
                return ""
            if settings.buyee.affiliate_id:
                url += ("&" if "?" in url else "?") + f"aid={settings.buyee.affiliate_id}"
            return url
        if source is None:
            return ""
        return source.buy_link(listing.listing_id, settings.buyee.affiliate_id)

    return Scanner(
        adapters=adapters,
        keywords=keywords,
        filters=FilterEngine(
            keywords=keywords,
            globals_=GlobalFilters.build(
                min_price=f.min_price, max_price=f.max_price,
                exclude=f.exclude, exclude_regex=f.exclude_regex,
                conditions=f.conditions, max_age_seconds=f.max_age_seconds,
            ),
        ),
        hub=hub,
        database=database,
        bus=bus,
        scheduler=Scheduler(
            intervals=s.intervals,
            budget_per_second=s.budget_per_second,
            jitter=s.jitter,
        ),
        scoring=ScoringEngine(ScoringConfig(
            bargain_price=settings.scoring.bargain_price,
            expensive_price=settings.scoring.expensive_price,
        )),
        currency=CurrencyConverter(
            target=settings.currency.target,
            fixed_rate=settings.currency.fixed_rate,
            ttl=settings.currency.ttl_seconds,
            allow_network=settings.currency.allow_network,
        ),
        metrics=metrics or Metrics(),
        dedup=Deduplicator(capacity=settings.storage.dedup_capacity),
        warmup=s.warmup,
        flush_every=settings.storage.flush_every,
        max_catchup_pages=s.max_catchup_pages,
        page_size=max((spec.page_size for spec in settings.sources.values()), default=60),
        buy_url_builder=buy_url_builder,
        notify_min_score=settings.scoring.notify_min_score,
    )
