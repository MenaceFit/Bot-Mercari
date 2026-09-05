"""Objets de test partagés."""

import time

from buyee_radar.adapters.base import (
    AdapterHealth, Listing, SearchQuery, SearchResult, SupportLevel,
)


def make_listing(listing_id="m1", title="ナイキ ディビジョン ジャケット", **kwargs):
    now = kwargs.pop("now", time.time())
    return Listing(
        source=kwargs.pop("source", "test"),
        listing_id=listing_id,
        title=title,
        url=f"https://example.invalid/{listing_id}",
        price=kwargs.pop("price", 8900),
        created_at=kwargs.pop("created_at", now - 2.0),
        requested_at=kwargs.pop("requested_at", now - 0.15),
        detected_at=kwargs.pop("detected_at", now),
        **kwargs,
    )


class ScriptedAdapter:
    """Adapter piloté par le test : rejoue des pages, enregistre les requêtes."""

    support = SupportLevel.VERIFIED
    support_note = ""

    def __init__(self, source="scripted", pages=None, error=None, fail=False):
        self.source = source
        self.label = source
        self.pages = list(pages or [])
        self.queries: list[SearchQuery] = []
        self.error = error
        self.fail = fail
        self.started = False

    async def start(self):
        self.started = True

    async def stop(self):
        self.started = False

    async def search(self, query):
        self.queries.append(query)
        if self.error is not None:
            error, self.error = self.error, None
            raise error
        if self.fail:
            return SearchResult(source=self.source, ok=False, error="panne simulée")
        now = time.time()
        if not self.pages:
            return SearchResult(source=self.source, requested_at=now, received_at=now)
        page = self.pages.pop(0)
        if isinstance(page, SearchResult):
            return page
        return SearchResult(
            source=self.source, listings=page,
            requested_at=now - 0.1, received_at=now,
        )

    async def fetch_latest(self, query):
        return await self.search(query)

    async def health_check(self):
        return AdapterHealth(source=self.source, ok=not self.fail, detail="scripted")


class CaptureNotifier:
    name = "capture"
    enabled = True

    def __init__(self, delay=0.0, fail_times=0):
        self.received = []
        self.delay = delay
        self.fail_times = fail_times
        self.attempts = 0

    async def send(self, listing):
        import asyncio
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise RuntimeError("échec simulé")
        if self.delay:
            await asyncio.sleep(self.delay)
        self.received.append(listing)

    async def close(self):
        pass
