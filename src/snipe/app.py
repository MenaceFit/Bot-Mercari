"""Assemblage : de la configuration à un scanner prêt à tourner.

Ce module est le seul endroit qui connaît toutes les pièces à la fois. Le
scanner, lui, ne connaît que des interfaces — c'est ce qui permet de le
tester sans réseau, sans base et sans Telegram.
"""

from __future__ import annotations

import logging
from typing import Any

from .config.settings import Settings
from .core.deduplicator import Deduplicator
from .core.filters import FilterEngine, GlobalFilters
from .core.keywords import Keyword
from .core.metrics import Metrics
from .core.scanner import Scanner
from .core.scheduler import Scheduler
from .notifications.base import NotificationHub
from .notifications.console import ConsoleNotifier
from .notifications.discord import DiscordNotifier
from .notifications.telegram import TelegramNotifier
from .sources.base import BaseSource
from .sources.buyee import DEFAULT_TEMPLATES, buy_url, make_buyee_source
from .sources.html_source import SelectorSpec
from .sources.mercari import MercariSource
from .sources.simulator import SimulatorSource
from .sources.yahoo_auction import make_yahoo_auction_source

log = logging.getLogger(__name__)


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


def build_sources(settings: Settings, *, force_simulator: bool = False) -> dict[str, BaseSource]:
    """Instancie les sources activées.

    `force_simulator` sert au mode démo et au benchmark : aucune requête ne
    part vers l'extérieur, mais tout le pipeline en aval est exercé.
    """
    if force_simulator:
        return {"simulator": SimulatorSource(seed=1234)}

    sources: dict[str, BaseSource] = {}
    for name, spec in settings.sources.items():
        if not spec.enabled:
            continue
        try:
            source = _make_source(name, spec)
        except Exception as exc:
            # Une source mal configurée ne doit pas empêcher les autres de
            # démarrer — c'est le critère « les sources sont indépendantes ».
            log.error("source « %s » ignorée : %s", name, exc)
            continue
        if source is not None:
            sources[source.name] = source

    if not sources:
        log.warning(
            "aucune source active — active au moins une source dans config.yaml, "
            "ou lance avec --demo pour utiliser le simulateur"
        )
    return sources


def _make_source(name: str, spec) -> BaseSource | None:
    common = {
        "max_concurrent": spec.max_concurrent,
        "connect_timeout": spec.connect_timeout,
        "read_timeout": spec.read_timeout,
        "total_timeout": spec.total_timeout,
    }
    if name == "mercari":
        return MercariSource(max_retries=spec.max_retries, **common)
    if name == "simulator":
        return SimulatorSource()
    if name == "yahoo_auction":
        return make_yahoo_auction_source(
            search_url=spec.search_url or make_yahoo_auction_source().search_url,
            selectors=_selectors(spec) ,
            max_retries=spec.max_retries,
            **common,
        )
    if name.startswith("buyee"):
        return make_buyee_source(
            marketplace=spec.marketplace or "mercari",
            search_url=spec.search_url,
            selectors=_selectors(spec),
            max_retries=spec.max_retries,
            **common,
        )
    log.error("source inconnue dans config.yaml : « %s »", name)
    return None


def _selectors(spec) -> SelectorSpec | None:
    if not spec.selectors:
        return None
    known = set(SelectorSpec.__dataclass_fields__)
    return SelectorSpec(**{k: v for k, v in spec.selectors.items() if k in known})


def build_hub(
    settings: Settings,
    *,
    dry_run: bool = False,
    on_sent=None,
    metrics: Metrics | None = None,
) -> NotificationHub:
    notifiers: list[Any] = []
    n = settings.notifications

    if n.console:
        notifiers.append(ConsoleNotifier())
    if n.telegram_enabled:
        notifiers.append(
            TelegramNotifier(
                settings.telegram_token,
                settings.telegram_chat_id,
                enabled=True,
                send_photo=n.telegram_photo,
                silent=n.telegram_silent,
            )
        )
    if n.discord_enabled:
        notifiers.append(DiscordNotifier(settings.discord_webhook, enabled=True))

    def record(listing, channel, ok, latency_ms, error):
        # Les métriques de notification passent par ici : sans ce câblage,
        # le bloc d'état afficherait « 0 envoyée » alors que tout fonctionne.
        if metrics is not None:
            if ok:
                metrics.notifications.inc()
                metrics.notify.add(listing.notify_ms or latency_ms)
                if listing.total_ms:
                    metrics.total.add(listing.total_ms)
            else:
                metrics.notification_failures.inc()
        if on_sent is not None:
            on_sent(listing, channel, ok, latency_ms, error)

    return NotificationHub(
        notifiers,
        max_queue=n.max_queue,
        max_retries=n.max_retries,
        dry_run=dry_run,
        on_sent=record,
    )


def build_filters(settings: Settings, keywords: list[Keyword]) -> FilterEngine:
    f = settings.filters
    return FilterEngine(
        keywords=keywords,
        globals_=GlobalFilters.build(
            min_price=f.min_price,
            max_price=f.max_price,
            exclude=f.exclude,
            exclude_regex=f.exclude_regex,
            conditions=f.conditions,
            max_age_seconds=f.max_age_seconds,
        ),
    )


def build_scanner(
    settings: Settings,
    sources: dict[str, BaseSource],
    storage,
    hub: NotificationHub,
    metrics: Metrics | None = None,
) -> Scanner:
    keywords = build_keywords(settings)
    s = settings.scanner

    def buy_url_builder(listing):
        if not settings.buyee.enabled:
            return ""
        return buy_url(
            listing.source,
            listing.id,
            templates={**DEFAULT_TEMPLATES, **settings.buyee.templates},
            affiliate_id=settings.buyee.affiliate_id,
        )

    return Scanner(
        sources=sources,
        keywords=keywords,
        filters=build_filters(settings, keywords),
        hub=hub,
        storage=storage,
        scheduler=Scheduler(
            intervals=s.intervals,
            budget_per_second=s.budget_per_second,
            jitter=s.jitter,
        ),
        metrics=metrics or Metrics(),
        dedup=Deduplicator(capacity=settings.storage.dedup_capacity),
        warmup=s.warmup,
        max_catchup_pages=s.max_catchup_pages,
        page_size=max(
            (spec.page_size for spec in settings.sources.values()), default=60
        ),
        buy_url_builder=buy_url_builder,
    )
