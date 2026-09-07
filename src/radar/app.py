"""Assemblage : de la configuration à un scanner prêt à tourner.

Seul endroit qui connaît toutes les pièces à la fois. Le scanner, lui, ne
connaît que des interfaces — c'est ce qui permet de le tester sans réseau,
sans base et sans Telegram.
"""

from __future__ import annotations

import logging
from typing import Any

from .adapters.base import MarketplaceAdapter
from .adapters.mercari_dual import MercariSource
from .adapters.simulator import PROFILES, SimulatorAdapter
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
            enabled=spec.enabled,
        )
        for spec in settings.keywords
    ]


def build_adapters(
    settings: Settings, *, demo: bool = False
) -> dict[str, MarketplaceAdapter]:
    """Les sources à interroger.

    Il n'y en a qu'une : Mercari Japon, via son API de recherche. Le
    dictionnaire reste la forme attendue par le scanner — il sait déjà
    interroger plusieurs sources en parallèle, et le mode démo s'y branche
    sans cas particulier.
    """
    if demo:
        return {
            name: SimulatorAdapter(name, seed=1234 + index)
            for index, name in enumerate(PROFILES)
        }

    m = settings.mercari
    return {
        "mercari": MercariSource(
            mode=m.mode,
            max_concurrent=m.max_concurrent,
            connect_timeout=m.connect_timeout,
            read_timeout=m.read_timeout,
            total_timeout=m.total_timeout,
            max_retries=m.max_retries,
            proxy=m.proxy or None,
        )
    }


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
                enabled=True, send_photo=n.telegram_photo,
                silent=n.telegram_silent, style=n.telegram_style,
                topic_id=settings.telegram_topic_id,
                min_score=settings.scoring.telegram_min_score,
            )
        )
    if n.discord_enabled:
        notifiers.append(DiscordNotifier(
            settings.discord_webhook, enabled=True,
            thread_id=settings.discord_thread_id,
            min_score=settings.scoring.discord_min_score,
        ))

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
        """L'annonce se voit et s'achète au même endroit : jp.mercari.com.

        Aucun intermédiaire n'est intercalé — un lien inventé vers un
        service de réexpédition mènerait à une page morte.
        """
        return listing.url

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
        page_size=settings.mercari.page_size,
        buy_url_builder=buy_url_builder,
        notify_min_score=settings.scoring.notify_min_score,
    )
