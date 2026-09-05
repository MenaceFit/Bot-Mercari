"""Source générique pilotée par sélecteurs CSS.

Pourquoi ce module existe plutôt qu'un scraper écrit en dur
-----------------------------------------------------------
Yahoo Auctions n'expose plus d'API de recherche publique, et Buyee n'en a
jamais eu. Les surveiller demande donc de lire du HTML.

Or `auctions.yahoo.co.jp` et `buyee.jp` sont inaccessibles depuis
l'environnement où ce code a été écrit : la passerelle réseau refuse le
CONNECT. Écrire `node.css_first("div.Product__titleLink")` sans avoir jamais
vu la page produirait du code d'apparence crédible et faux — et un scraper
faux ne lève pas d'erreur, il renvoie simplement zéro annonce, en silence.

D'où ce choix : la structure de la page est une DONNÉE, pas du code. Les
sélecteurs vivent dans `config.yaml`, se corrigent en trente secondes quand
la marketplace change son HTML (ce qui arrive), et la même classe sert
n'importe quel site. Le code, lui, est réel et testé sur des fixtures.

Un `SelectorSpec` incomplet est refusé au démarrage plutôt que de produire
une source qui tourne à vide.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urljoin

import httpx

from .base import Listing, SearchQuery, SearchResult, SourceError, SourceHealth

log = logging.getLogger(__name__)

_DIGITS_RE = re.compile(r"[\d,\.]+")
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class SelectorError(ValueError):
    """Configuration de sélecteurs inutilisable."""


@dataclass(slots=True)
class SelectorSpec:
    """Comment lire une page de résultats. Tout vient du YAML.

    `item` et `title` sont obligatoires : sans eux il n'y a pas d'annonce à
    extraire. Le reste est optionnel — une marketplace qui n'affiche pas le
    vendeur en liste n'empêche pas le reste de fonctionner.
    """

    item: str                       # conteneur d'une annonce
    title: str                      # texte du titre
    link: str = ""                  # <a href> ; défaut : le premier lien
    price: str = ""
    image: str = ""
    seller: str = ""
    item_id_attr: str = ""          # attribut portant l'identifiant
    item_id_from_link: str = ""     # ou regex sur l'URL, groupe 1 = id
    next_page: str = ""             # lien « page suivante »

    def validate(self) -> None:
        if not self.item or not self.title:
            raise SelectorError(
                "les sélecteurs 'item' et 'title' sont obligatoires — sans eux "
                "la source tournerait à vide sans jamais lever d'erreur"
            )
        if not self.item_id_attr and not self.item_id_from_link:
            raise SelectorError(
                "il faut 'item_id_attr' ou 'item_id_from_link' : sans identifiant "
                "stable, la déduplication renotifierait la même annonce à chaque scan"
            )


@dataclass
class HTMLSource:
    """Scrape une marketplace HTML selon des sélecteurs configurés."""

    name: str
    search_url: str                 # gabarit, ex. "https://x/search?p={keyword}"
    selectors: SelectorSpec
    base_url: str = ""
    #: Toujours False : aucune de ces sources n'a pu être exercée en réel
    #: depuis l'environnement de développement. Le scanner l'annonce au
    #: démarrage au lieu de laisser croire que tout est éprouvé.
    verified: bool = False
    extra_params: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    max_concurrent: int = 4
    connect_timeout: float = 5.0
    read_timeout: float = 10.0
    total_timeout: float = 15.0
    max_retries: int = 2
    currency: str = "JPY"

    _client: httpx.AsyncClient | None = field(default=None, repr=False)
    _gate: asyncio.Semaphore | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.selectors.validate()
        if "{keyword}" not in self.search_url:
            raise SelectorError("search_url doit contenir « {keyword} »")

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                http2=True,
                follow_redirects=True,
                timeout=httpx.Timeout(
                    self.total_timeout,
                    connect=self.connect_timeout,
                    read=self.read_timeout,
                ),
                limits=httpx.Limits(
                    max_connections=self.max_concurrent,
                    max_keepalive_connections=self.max_concurrent,
                    keepalive_expiry=90.0,
                ),
                headers={
                    "User-Agent": _UA,
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "ja-JP,ja;q=0.9",
                    **self.headers,
                },
            )
            self._gate = asyncio.Semaphore(self.max_concurrent)
        return self._client

    def build_url(self, query: SearchQuery) -> str:
        if query.cursor:
            return query.cursor
        url = self.search_url.replace("{keyword}", _quote(query.text))
        params = dict(self.extra_params)
        if query.min_price and "{min_price}" not in self.search_url:
            params.setdefault("price_min", str(query.min_price))
        if query.max_price and "{max_price}" not in self.search_url:
            params.setdefault("price_max", str(query.max_price))
        url = url.replace("{min_price}", str(query.min_price or ""))
        url = url.replace("{max_price}", str(query.max_price or ""))
        if params:
            url += ("&" if "?" in url else "?") + urlencode(params)
        return url

    async def search(self, query: SearchQuery) -> SearchResult:
        client = self._ensure_client()
        url = self.build_url(query)
        assert self._gate is not None

        async with self._gate:
            last: Exception | None = None
            for attempt in range(self.max_retries + 1):
                requested_at = time.time()
                try:
                    response = await client.get(url)
                except httpx.HTTPError as exc:
                    last = exc
                    if attempt < self.max_retries:
                        await asyncio.sleep(0.3 * (2 ** attempt))
                        continue
                    raise SourceError(f"réseau : {exc}") from exc

                received_at = time.time()
                if response.status_code == 429:
                    raise SourceError(
                        f"{self.name} : limite de débit (429)",
                        status=429,
                        retry_after=_retry_after(response),
                    )
                if response.status_code >= 500 and attempt < self.max_retries:
                    await asyncio.sleep(0.3 * (2 ** attempt))
                    continue
                if response.status_code != 200:
                    raise SourceError(
                        f"HTTP {response.status_code} sur {self.name}",
                        status=response.status_code,
                    )

                return self.parse(response.text, requested_at, received_at, url)

        raise SourceError(f"échec après {self.max_retries + 1} tentatives : {last}")

    # ── Extraction ────────────────────────────────────────────────────────
    def parse(
        self, html: str, requested_at: float, received_at: float, page_url: str = ""
    ) -> SearchResult:
        """Extrait les annonces. Testable sans réseau, sur du HTML fixture."""
        from selectolax.parser import HTMLParser

        tree = HTMLParser(html)
        sel = self.selectors
        base = self.base_url or page_url
        listings: list[Listing] = []

        for node in tree.css(sel.item):
            try:
                listing = self._listing(node, base, requested_at, received_at)
            except Exception:
                log.debug("%s : annonce ignorée (extraction)", self.name, exc_info=True)
                continue
            if listing is not None:
                listings.append(listing)

        cursor = ""
        if sel.next_page:
            node = tree.css_first(sel.next_page)
            if node is not None:
                href = node.attributes.get("href") or ""
                cursor = urljoin(base, href) if href else ""

        if not listings:
            # Zéro annonce sur une page qui a répondu 200 est presque toujours
            # un sélecteur périmé, pas un marché vide. Le dire fort évite des
            # heures de « pourquoi il ne trouve rien ».
            log.warning(
                "%s : aucune annonce extraite — les sélecteurs CSS sont "
                "probablement à mettre à jour dans config.yaml (item=%r)",
                self.name, sel.item,
            )

        return SearchResult(
            listings=listings,
            cursor=cursor,
            requested_at=requested_at,
            received_at=received_at,
        )

    def _listing(
        self, node: Any, base: str, requested_at: float, received_at: float
    ) -> Listing | None:
        sel = self.selectors

        title_node = node.css_first(sel.title) if sel.title else None
        title = _text(title_node) or _text(node)
        if not title:
            return None

        link_node = node.css_first(sel.link) if sel.link else node.css_first("a")
        href = (link_node.attributes.get("href") or "") if link_node is not None else ""
        url = urljoin(base, href) if href else ""

        item_id = ""
        if sel.item_id_attr:
            item_id = (node.attributes.get(sel.item_id_attr) or "").strip()
            if not item_id and link_node is not None:
                item_id = (link_node.attributes.get(sel.item_id_attr) or "").strip()
        if not item_id and sel.item_id_from_link and url:
            match = re.search(sel.item_id_from_link, url)
            if match:
                item_id = match.group(1)
        if not item_id:
            # Pas d'identifiant stable = déduplication impossible = spam à
            # chaque scan. Mieux vaut perdre l'annonce que la renotifier
            # indéfiniment.
            return None

        return Listing(
            id=item_id,
            source=self.name,
            title=title,
            url=url,
            price=_price(_text(node.css_first(sel.price)) if sel.price else ""),
            currency=self.currency,
            image_url=_image(node.css_first(sel.image), base) if sel.image else "",
            seller=_text(node.css_first(sel.seller)) if sel.seller else "",
            # Les pages de liste n'affichent presque jamais l'heure exacte de
            # publication. `published_at` reste donc à 0 = inconnu, et la
            # latence de détection ne sera pas calculée pour cette source
            # plutôt que d'être inventée.
            published_at=0.0,
            requested_at=requested_at,
            received_at=received_at,
        )

    async def health_check(self) -> SourceHealth:
        started = time.perf_counter()
        try:
            result = await self.search(SearchQuery(text="nike", limit=1))
        except SourceError as exc:
            return SourceHealth(ok=False, detail=str(exc))
        elapsed = int((time.perf_counter() - started) * 1000)
        if not len(result):
            return SourceHealth(
                ok=False,
                detail="page reçue mais aucune annonce extraite — sélecteurs à revoir",
                latency_ms=elapsed,
            )
        return SourceHealth(ok=True, detail=f"{len(result)} annonce(s)", latency_ms=elapsed)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# ── Utilitaires d'extraction ──────────────────────────────────────────────
def _text(node: Any) -> str:
    if node is None:
        return ""
    return " ".join((node.text() or "").split())


def _image(node: Any, base: str) -> str:
    if node is None:
        return ""
    attrs = node.attributes
    # `data-src` d'abord : les listes en chargement paresseux mettent un GIF
    # transparent dans `src` et la vraie image dans `data-src`.
    for key in ("data-src", "data-original", "src"):
        value = attrs.get(key)
        if value:
            return urljoin(base, value)
    return ""


#: « 1.200 » et « 1,200 » veulent dire mille deux cents, pas 1,2. Un
#: séparateur suivi d'exactement trois chiffres est un séparateur de milliers.
_THOUSANDS_RE = re.compile(r"[.,](?=\d{3}(?:\D|$))")


def _price(text: str) -> int:
    """« 8,900円 », « ¥12 500 », « 1.200 yen » → un entier.

    Les marketplaces japonaises séparent les milliers par une virgule, mais
    les pages traduites automatiquement produisent parfois un point. Traiter
    « 1.200 » comme 1,2 puis tronquer donnerait un prix de 1 yen — et un
    filtre de prix minimum écarterait alors l'annonce sans rien dire.
    """
    if not text:
        return 0
    match = _DIGITS_RE.search(text.replace(" ", "").replace("\u00a0", ""))
    if not match:
        return 0
    raw = _THOUSANDS_RE.sub("", match.group(0))
    try:
        return max(0, int(float(raw)))
    except ValueError:
        return 0


def _quote(text: str) -> str:
    from urllib.parse import quote
    return quote(text, safe="")


def _retry_after(response: httpx.Response) -> float:
    raw = response.headers.get("Retry-After")
    try:
        return max(0.5, float(raw)) if raw else 5.0
    except ValueError:
        return 5.0
