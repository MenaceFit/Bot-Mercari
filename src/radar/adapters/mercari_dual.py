"""Choisit entre la page de recherche et l'API, et ne se tait jamais.

Le scraping de `jp.mercari.com/search` est le chemin demandé. Mais il
dépend de la forme HTML servie, que je n'ai jamais pu observer : si
Mercari monte sa liste en JavaScript, aucun lecteur HTTP n'en tirera quoi
que ce soit.

D'où ce garde-fou : en mode `auto`, la première recherche essaie la page.
Si elle ne rend rien exploitable, on bascule sur l'API — une fois, en le
disant — et le bot continue de trouver des annonces au lieu de se taire.
C'est la panne qui a coûté le plus de temps à ce projet ; elle ne doit pas
pouvoir revenir par une porte différente.

Modes :
    web    la page uniquement. Si elle ne donne rien, la source est en
           erreur, visiblement — pas de repli.
    api    l'API uniquement.
    auto   la page d'abord, l'API en secours (défaut).
"""

from __future__ import annotations

import logging

from .base import AdapterHealth, SearchQuery, SearchResult, SupportLevel
from .mercari import MercariAdapter
from .mercari_web import MercariWebAdapter

log = logging.getLogger(__name__)

MODES = ("auto", "web", "api")


class MercariSource:
    source = "mercari"
    label = "Mercari Japon"

    def __init__(self, *, mode: str = "auto", **kwargs) -> None:
        self.mode = mode if mode in MODES else "auto"
        self.web = MercariWebAdapter(**kwargs) if self.mode != "api" else None
        self.api = MercariAdapter(**kwargs) if self.mode != "web" else None
        #: Chemin réellement utilisé au dernier scan — affiché tel quel.
        self.active = "page de recherche" if self.web else "API"
        self._fell_back = False

    @property
    def support(self) -> SupportLevel:
        return SupportLevel.VERIFIED if self.mode == "api" else SupportLevel.URL_VERIFIED

    @property
    def support_note(self) -> str:
        if self.mode == "web":
            return "Page de recherche publique, triée par date de publication."
        if self.mode == "api":
            return "API de recherche officielle, triée par date de publication."
        return (
            "Page de recherche publique, avec repli automatique sur l'API "
            "si la page ne rend rien d'exploitable."
        )

    async def start(self) -> None:
        for adapter in (self.web, self.api):
            if adapter is not None:
                await adapter.start()

    async def stop(self) -> None:
        for adapter in (self.web, self.api):
            if adapter is not None:
                await adapter.stop()

    async def search(self, query: SearchQuery) -> SearchResult:
        if self.web is not None and not self._fell_back:
            result = await self.web.search(query)
            if result.ok:
                self.active = f"page de recherche ({self.web.strategy})"
                return result
            if self.mode == "web":
                self.active = "page de recherche"
                return result
            # Une seule bascule, annoncée une seule fois.
            self._fell_back = True
            log.warning(
                "la page de recherche ne rend rien d'exploitable (%s) — "
                "bascule sur l'API Mercari. Lance « radar scrape-test » pour "
                "voir ce que la page contient vraiment.",
                result.error or "sans détail",
            )

        if self.api is None:
            return SearchResult(
                source=self.source, ok=False,
                error="mode « web » et la page ne rend rien",
            )
        self.active = "API"
        return await self.api.search(query)

    async def fetch_latest(self, query: SearchQuery) -> SearchResult:
        return await self.search(query)

    async def health_check(self) -> AdapterHealth:
        adapter = self.api if (self._fell_back or self.web is None) else self.web
        health = await adapter.health_check()
        health.detail = f"{health.detail} — via {self.active}"
        health.support = self.support
        return health
