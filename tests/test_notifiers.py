"""Notificateur Discord : file non bloquante, embeds, gestion des 429."""

import asyncio

import httpx
import pytest

from mercari_sniper.models import Listing
from mercari_sniper.notifiers import ConsoleNotifier, DiscordNotifier

WEBHOOK = "https://discord.com/api/webhooks/1/fake-pour-test"


def make_listing(rarity="RARE", **kwargs):
    defaults = dict(
        id="m1",
        title="ナイキ トレイル ジャケット",
        price=8900,
        url="https://jp.mercari.com/item/m1",
        image="https://static.mercdn.net/a.jpg",
        matched=["ナイキ トレイル"],
        rarity=rarity,
        rarity_color=0xF5A623,
        created=1000,
        detected_at=1003,
    )
    defaults.update(kwargs)
    return Listing(**defaults)


def mock_transport(handler):
    """Remplace le réseau par un handler local."""
    return httpx.MockTransport(handler)


class TestEmbed:
    def test_embed_carries_the_essentials(self):
        notifier = DiscordNotifier(WEBHOOK)
        embed = notifier._build_embed(make_listing())

        assert "ナイキ トレイル ジャケット" in embed["title"]
        assert "RARE" in embed["title"]
        assert embed["url"] == "https://jp.mercari.com/item/m1"
        assert embed["color"] == 0xF5A623

        fields = {field["name"]: field["value"] for field in embed["fields"]}
        assert fields["💰 Prix"] == "¥8,900"
        assert fields["⚡ Détecté en"] == "3.0s"

    def test_image_included_when_valid(self):
        notifier = DiscordNotifier(WEBHOOK)
        assert notifier._build_embed(make_listing())["image"]["url"].startswith("http")

    def test_image_omitted_when_absent(self):
        notifier = DiscordNotifier(WEBHOOK)
        assert "image" not in notifier._build_embed(make_listing(image=""))

    def test_long_title_is_truncated(self):
        notifier = DiscordNotifier(WEBHOOK)
        embed = notifier._build_embed(make_listing(title="あ" * 500))
        assert len(embed["title"]) < 260   # sous la limite Discord

    def test_extra_keywords_are_summarised(self):
        notifier = DiscordNotifier(WEBHOOK)
        embed = notifier._build_embed(
            make_listing(matched=[f"kw{i}" for i in range(9)])
        )
        assert "+5" in embed["description"]

    def test_unknown_latency_renders_na(self):
        notifier = DiscordNotifier(WEBHOOK)
        embed = notifier._build_embed(make_listing(created=0))
        fields = {f["name"]: f["value"] for f in embed["fields"]}
        assert fields["⚡ Détecté en"] == "n/a"


class TestQueueing:
    def test_notify_never_blocks(self):
        notifier = DiscordNotifier(WEBHOOK, max_queue=10)
        notifier.notify(make_listing())          # aucun worker démarré
        assert notifier._queue.qsize() == 1

    def test_overflow_is_dropped_not_awaited(self):
        notifier = DiscordNotifier(WEBHOOK, max_queue=2)
        for _ in range(5):
            notifier.notify(make_listing())
        assert notifier._queue.qsize() == 2
        assert notifier.dropped == 3

    def test_empty_webhook_is_a_noop(self):
        notifier = DiscordNotifier("")
        notifier.notify(make_listing())
        assert notifier._queue.qsize() == 0

    def test_rarity_threshold_filters(self):
        notifier = DiscordNotifier(WEBHOOK, min_rarity="ULTRA RARE")
        notifier.notify(make_listing(rarity="PREMIUM"))
        notifier.notify(make_listing(rarity="RARE"))
        assert notifier._queue.qsize() == 0

        notifier.notify(make_listing(rarity="ULTRA RARE"))
        assert notifier._queue.qsize() == 1


class TestSending:
    async def test_successful_send_counts(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(204)

        notifier = DiscordNotifier(WEBHOOK, rate_limit=100)
        notifier._client = httpx.AsyncClient(transport=mock_transport(handler))
        await notifier._send(make_listing())

        assert notifier.sent == 1
        assert len(calls) == 1
        await notifier._client.aclose()

    async def test_retries_after_429(self):
        responses = [
            httpx.Response(429, json={"retry_after": 0.01}),
            httpx.Response(204),
        ]

        def handler(request):
            return responses.pop(0)

        notifier = DiscordNotifier(WEBHOOK)
        notifier._client = httpx.AsyncClient(transport=mock_transport(handler))
        await notifier._send(make_listing())

        assert notifier.sent == 1
        assert responses == []
        await notifier._client.aclose()

    async def test_client_error_is_not_retried(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(404, text="unknown webhook")

        notifier = DiscordNotifier(WEBHOOK)
        notifier._client = httpx.AsyncClient(transport=mock_transport(handler))
        await notifier._send(make_listing())

        assert notifier.failed == 1
        assert len(calls) == 1     # un webhook supprimé ne se répare pas
        await notifier._client.aclose()

    async def test_worker_drains_the_queue(self):
        received = []

        def handler(request):
            received.append(request)
            return httpx.Response(204)

        notifier = DiscordNotifier(WEBHOOK, rate_limit=1000)
        notifier._client = httpx.AsyncClient(transport=mock_transport(handler))
        await notifier.start()
        try:
            for i in range(3):
                notifier.notify(make_listing(id=f"m{i}"))
            await asyncio.wait_for(notifier._queue.join(), timeout=5)
        finally:
            await notifier.stop()

        assert len(received) == 3
        assert notifier.sent == 3

    async def test_stats_shape(self):
        notifier = DiscordNotifier(WEBHOOK)
        assert set(notifier.stats()) == {"sent", "failed", "dropped", "queued"}
        await notifier.stop()


class TestConsoleNotifier:
    async def test_notify_does_not_raise(self):
        notifier = ConsoleNotifier(color=False)
        await notifier.start()
        notifier.notify(make_listing())
        notifier.notify(make_listing(created=0, matched=[]))
        await notifier.stop()
