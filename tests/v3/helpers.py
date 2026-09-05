"""Objets de test partagés : annonces et source scriptée."""

import time

from snipe.sources.base import Listing, SearchQuery, SearchResult


def make_listing(item_id="m1", title="ナイキ ディビジョン ジャケット", **kwargs):
    now = kwargs.pop("now", time.time())
    return Listing(
        id=item_id,
        source=kwargs.pop("source", "test"),
        title=title,
        url=f"https://example.invalid/{item_id}",
        price=kwargs.pop("price", 8900),
        published_at=kwargs.pop("published_at", now - 2.0),
        requested_at=kwargs.pop("requested_at", now - 0.15),
        received_at=kwargs.pop("received_at", now),
        **kwargs,
    )


class ScriptedSource:
    """Source pilotée par le test : rejoue des pages, enregistre les requêtes."""

    name = "scripted"
    verified = True

    def __init__(self, pages=None, error=None):
        self.pages = list(pages or [])
        self.queries: list[SearchQuery] = []
        self.error = error
        self.closed = False

    async def search(self, query):
        self.queries.append(query)
        if self.error is not None:
            error, self.error = self.error, None
            raise error
        if not self.pages:
            return SearchResult(listings=[], requested_at=time.time(),
                                received_at=time.time())
        page = self.pages.pop(0)
        if isinstance(page, SearchResult):
            return page
        now = time.time()
        return SearchResult(listings=page, requested_at=now - 0.1, received_at=now)

    async def health_check(self):
        from snipe.sources.base import SourceHealth
        return SourceHealth(ok=True, detail="scripted")

    async def close(self):
        self.closed = True
