"""Assemblage : de la configuration à un scanner prêt à tourner.

Seul endroit qui connaît toutes les pièces à la fois. Le scanner, lui, ne
connaît que des interfaces — c'est ce qui permet de le tester sans réseau,
sans base et sans Telegram.
"""

from __future__ import annotations

import logging
from typing import Any

from .adapters.base import MarketplaceAdapter, SupportLevel
from .adapters.buyee_html import MARKETPLACES, BuyeeAdapter, Selectors
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

#: Gabarits d'URL d'achat Buyee. NON VÉRIFIÉS : buyee.jp est inaccessible
#: depuis l'environnement de développement. Surchargeables dans radar.yaml.
BUY_TEMPLATES: dict[str, str] = {
    "mercari": "https://buyee.jp/mercari/item/{id}",
    "rakuma": "https://buyee.jp/rakuma/item/{id}",
    "jdi_auction": "https://buyee.jp/item/jdirectitems/auction/{id}",
    "jdi_fleamarket": "https://buyee.jp/paypayfleamarket/item/{id}",
}


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


def build_adapters(
    settings: Settings, *, demo: bool = False
) -> dict[str, MarketplaceAdapter]:
    """Instancie les adapters activés.

    Un adapter mal configuré est ignoré avec un message clair — il ne doit
    pas empêcher les autres de démarrer (critère « sources indépendantes »).
    """
    if demo:
        return {
            name: SimulatorAdapter(name, seed=1234 + index)
            for index, name in enumerate(PROFILES)
        }

    adapters: dict[str, MarketplaceAdapter] = {}
    for name, spec in settings.sources.items():
        if not spec.enabled:
            continue
        if name.startswith("sim_"):
            adapters[name] = SimulatorAdapter(name)
            continue

        market = MARKETPLACES.get(name)
        if market is None:
            log.error(
                "marketplace inconnue dans radar.yaml : « %s » — connues : %s",
                name, ", ".join(sorted(MARKETPLACES)),
            )
            continue
        if market.support is SupportLevel.UNSUPPORTED:
            log.warning(
                "source « %s » activée mais NON SUPPORTÉE — %s",
                name, market.support_note,
            )
            continue

        known = set(Selectors.__dataclass_fields__)
        adapters[name] = BuyeeAdapter(
            market,
            selectors=Selectors(
                **{k: v for k, v in (spec.selectors or {}).items() if k in known}
            ),
            search_url=spec.search_url,
            extra_params=spec.extra_params,
            max_concurrent=spec.max_concurrent,
            connect_timeout=spec.connect_timeout,
            read_timeout=spec.read_timeout,
            total_timeout=spec.total_timeout,
            max_retries=spec.max_retries,
        )

    if not adapters:
        log.warning(
            "aucune source active — active une marketplace dans radar.yaml, "
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
        if not settings.buyee.enabled:
            return ""
        template = {**BUY_TEMPLATES, **settings.buyee.templates}.get(listing.source)
        if not template or not listing.listing_id:
            return ""
        try:
            url = template.format(id=listing.listing_id)
        except (KeyError, IndexError, ValueError):
            return ""
        if settings.buyee.affiliate_id:
            url += ("&" if "?" in url else "?") + f"aid={settings.buyee.affiliate_id}"
        return url

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
