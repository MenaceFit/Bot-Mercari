"""Mercari Japon — lecture de la page de recherche publique.

    https://jp.mercari.com/search?keyword=…&sort=created_time&order=desc

C'est l'URL des nouvelles annonces : `sort=created_time&order=desc` place
les plus récentes en tête, `status=on_sale` écarte ce qui est déjà vendu.

Le problème, et comment il est traité
-------------------------------------
`jp.mercari.com` est une application Next.js. Selon la version servie, les
annonces se trouvent à trois endroits différents — ou nulle part :

1. **`__NEXT_DATA__`** — l'ancien routeur : un seul `<script>` JSON.
2. **Flux RSC** — le routeur App : des morceaux de JSON poussés par
   `self.__next_f.push([1, "…"])`, à recoller avant de les lire.
3. **Le DOM** — des liens `<a href="/item/m123…">` avec le titre et le prix
   à côté, quand la page est rendue côté serveur.
4. **Rien du tout** — si la liste est chargée par JavaScript après coup.
   Aucun scraper HTTP ne peut alors rien en tirer, quelle que soit son
   ingéniosité.

Les trois premières stratégies sont essayées dans l'ordre, et la source
dit LAQUELLE a fonctionné. Le quatrième cas ne se devine pas : il se
constate, avec `radar scrape-test`, qui va chercher la page et rapporte ce
que chaque stratégie a trouvé.

Je n'ai jamais pu charger cette page : elle est bloquée depuis
l'environnement où ce code est écrit. Les trois lecteurs sont donc écrits
à partir de la forme connue de Next.js, pas d'une page observée — et c'est
exactement pour ça que l'adapter refuse de faire semblant : s'il ne trouve
rien, il le dit, et `mode: auto` bascule sur l'API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Iterator
from urllib.parse import quote

import httpx

from .base import (
    AdapterError,
    AdapterHealth,
    Listing,
    SearchQuery,
    SearchResult,
    SupportLevel,
)

log = logging.getLogger(__name__)

SEARCH_URL = (
    "https://jp.mercari.com/search"
    "?keyword={keyword}&sort=created_time&order=desc&status=on_sale"
)
ITEM_URL = "https://jp.mercari.com/item/{id}"
SHOP_URL = "https://jp.mercari.com/shops/product/{id}"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

#: Un identifiant d'annonce Mercari : « m » suivi de chiffres.
_ITEM_ID = re.compile(r'"id"\s*:\s*"(m\d{6,})"')
#: Les morceaux du flux RSC.
_FLIGHT = re.compile(r'self\.__next_f\.push\(\s*\[\s*1\s*,\s*("(?:[^"\\]|\\.)*")')
#: Le script de l'ancien routeur.
_NEXT_DATA = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)
#: Liens d'annonce dans le DOM.
_ITEM_HREF = re.compile(r'href="(/item/(m\d{6,})[^"]*)"')
_PRICE_NEAR = re.compile(r"[¥￥]\s*([\d,\.]+)")


class MercariWebAdapter:
    """Lit la page de recherche publique, triée par date de publication."""

    source = "mercari"
    label = "Mercari Japon"
    support = SupportLevel.URL_VERIFIED
    support_note = (
        "Page de recherche publique, triée par date de publication. "
        "L'extraction dépend de la forme servie par Mercari — « radar "
        "scrape-test » dit ce qu'elle donne sur ta machine."
    )

    def __init__(
        self,
        *,
        max_concurrent: int = 4,
        connect_timeout: float = 5.0,
        read_timeout: float = 12.0,
        total_timeout: float = 20.0,
        max_retries: int = 2,
        proxy: str | None = None,
    ) -> None:
        self.max_retries = max(1, max_retries)
        self._max_concurrent = max(1, max_concurrent)
        self._timeout = httpx.Timeout(
            total_timeout, connect=connect_timeout, read=read_timeout
        )
        self._proxy = proxy
        self._client: httpx.AsyncClient | None = None
        self._gate: asyncio.Semaphore | None = None
        #: Stratégie qui a fonctionné au dernier scan. Affichée telle quelle.
        self.strategy = ""

    # ── Cycle de vie ──────────────────────────────────────────────────────
    async def start(self) -> None:
        if self._client is not None:
            return
        self._client = httpx.AsyncClient(
            http2=True,
            follow_redirects=True,
            timeout=self._timeout,
            limits=httpx.Limits(
                max_connections=self._max_concurrent,
                max_keepalive_connections=self._max_concurrent,
                keepalive_expiry=90.0,
            ),
            headers={
                "User-Agent": _UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
                "Upgrade-Insecure-Requests": "1",
            },
            proxy=self._proxy,
        )
        self._gate = asyncio.Semaphore(self._max_concurrent)

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ── Requête ───────────────────────────────────────────────────────────
    def build_url(self, query: SearchQuery) -> str:
        url = SEARCH_URL.format(keyword=quote(query.text, safe=""))
        if query.min_price:
            url += f"&price_min={int(query.min_price)}"
        if query.max_price:
            url += f"&price_max={int(query.max_price)}"
        return url

    async def search(self, query: SearchQuery) -> SearchResult:
        await self.start()
        assert self._client is not None and self._gate is not None
        url = self.build_url(query)

        async with self._gate:
            for attempt in range(self.max_retries):
                requested_at = time.time()
                try:
                    response = await self._client.get(url)
                except httpx.HTTPError as exc:
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(0.3 * (attempt + 1))
                        continue
                    return SearchResult(
                        source=self.source, ok=False, error=f"réseau : {exc}",
                        requested_at=requested_at, received_at=time.time(),
                    )

                received_at = time.time()
                code = response.status_code

                if code == 429:
                    raise AdapterError(
                        "limite de débit Mercari (429)",
                        status=429, retry_after=_retry_after(response),
                    )
                if code != 200:
                    return SearchResult(
                        source=self.source, ok=False,
                        error=f"HTTP {code}",
                        requested_at=requested_at, received_at=received_at,
                    )

                listings, strategy = extract(response.text, requested_at, received_at)
                self.strategy = strategy
                if not listings:
                    # Zéro annonce n'est pas forcément une panne — la
                    # recherche peut être vide. Mais si la page ne contient
                    # AUCUN identifiant d'annonce, c'est que la liste est
                    # montée en JavaScript, et ça, il faut le dire.
                    if "/item/m" not in response.text and '"id":"m' not in response.text:
                        return SearchResult(
                            source=self.source, ok=False,
                            error=("la page ne contient aucune annonce — la "
                                   "liste est chargée en JavaScript"),
                            requested_at=requested_at, received_at=received_at,
                        )
                return SearchResult(
                    source=self.source, listings=listings,
                    requested_at=requested_at, received_at=received_at,
                )

        return SearchResult(source=self.source, ok=False, error="épuisé")

    async def fetch_latest(self, query: SearchQuery) -> SearchResult:
        return await self.search(query)

    async def health_check(self) -> AdapterHealth:
        started = time.perf_counter()
        result = await self.search(SearchQuery(text="nike", limit=1))
        return AdapterHealth(
            source=self.source, ok=result.ok,
            detail=(result.error
                    or f"{len(result.listings)} annonce(s) via {self.strategy}"),
            latency_ms=round((time.perf_counter() - started) * 1000),
            support=self.support,
        )


# ══════════════════════════════════════════════════════════════════════════
#  Extraction
# ══════════════════════════════════════════════════════════════════════════
def extract(
    html: str, requested_at: float = 0.0, received_at: float = 0.0
) -> tuple[list[Listing], str]:
    """Les annonces de la page, et le nom de la stratégie qui a marché."""
    for name, reader in (
        ("__NEXT_DATA__", _from_next_data),
        ("flux RSC", _from_flight),
        ("DOM", _from_dom),
    ):
        try:
            listings = reader(html, requested_at, received_at)
        except Exception:      # noqa: BLE001 — une stratégie qui casse
            log.debug("stratégie « %s » en échec", name, exc_info=True)
            continue
        if listings:
            return listings, name
    return [], "aucune"


def _objects_with_item_id(blob: str) -> Iterator[dict[str, Any]]:
    """Les objets JSON de `blob` qui ressemblent à une annonce.

    On ne parse pas le document entier : il est énorme, et sa forme change.
    On part de chaque « "id":"m…" » et on remonte à l'accolade ouvrante,
    puis on avance jusqu'à la fermante correspondante. C'est robuste aux
    changements d'emboîtement, qui sont justement ce qui casse les
    scrapers écrits sur un chemin figé.
    """
    for match in _ITEM_ID.finditer(blob):
        start = blob.rfind("{", 0, match.start())
        if start < 0:
            continue
        depth, index, in_string, escaped = 0, start, False, False
        limit = min(len(blob), start + 20_000)
        while index < limit:
            char = blob[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        yield json.loads(blob[start:index + 1])
                    except ValueError:
                        pass
                    break
            index += 1


def _listing(raw: dict[str, Any], requested_at: float, received_at: float) -> Listing | None:
    item_id = str(raw.get("id") or "")
    if not (item_id.startswith("m") and item_id[1:].isdigit()):
        return None
    title = str(raw.get("name") or raw.get("productName") or "").strip()
    if not title:
        return None

    thumbs = raw.get("thumbnails") or raw.get("thumbnail") or []
    if isinstance(thumbs, str):
        thumbs = [thumbs]
    brand = raw.get("itemBrand")

    return Listing(
        source="mercari",
        listing_id=item_id,
        title=title,
        url=ITEM_URL.format(id=item_id),
        price=_int(raw.get("price")),
        currency="JPY",
        image_url=str(thumbs[0]) if thumbs and thumbs[0] else "",
        seller=str(raw.get("sellerId") or ""),
        category=str(raw.get("categoryId") or ""),
        condition=str(raw.get("itemConditionId") or ""),
        brand=str(brand.get("name") or "") if isinstance(brand, dict) else "",
        created_at=float(_int(raw.get("created") or raw.get("createdAt"))),
        updated_at=float(_int(raw.get("updated") or raw.get("updatedAt"))),
        requested_at=requested_at,
        detected_at=received_at,
    )


def _harvest(blob: str, requested_at: float, received_at: float) -> list[Listing]:
    listings: list[Listing] = []
    seen: set[str] = set()
    for raw in _objects_with_item_id(blob):
        listing = _listing(raw, requested_at, received_at)
        if listing and listing.listing_id not in seen:
            seen.add(listing.listing_id)
            listings.append(listing)
    return listings


def _from_next_data(html: str, requested_at: float, received_at: float) -> list[Listing]:
    match = _NEXT_DATA.search(html)
    return _harvest(match.group(1), requested_at, received_at) if match else []


def _from_flight(html: str, requested_at: float, received_at: float) -> list[Listing]:
    """Recolle les morceaux du flux RSC avant de les lire.

    Chaque `push` transporte un fragment de chaîne JavaScript ; un objet
    JSON peut être coupé en deux entre deux fragments. On concatène donc
    tout avant de chercher.
    """
    pieces: list[str] = []
    for match in _FLIGHT.finditer(html):
        try:
            pieces.append(json.loads(match.group(1)))
        except ValueError:
            continue
    return _harvest("".join(pieces), requested_at, received_at) if pieces else []


def _from_dom(html: str, requested_at: float, received_at: float) -> list[Listing]:
    """Repli : les liens d'annonce et ce qui les entoure.

    Moins riche — souvent sans date de publication, donc sans latence
    mesurable — mais ça vaut mieux que rien quand la page est rendue côté
    serveur sans données structurées.
    """
    listings: list[Listing] = []
    seen: set[str] = set()
    for match in _ITEM_HREF.finditer(html):
        item_id = match.group(2)
        if item_id in seen:
            continue
        seen.add(item_id)

        window = html[match.end():match.end() + 1200]
        title = ""
        for attr in ('alt="', 'aria-label="', 'title="'):
            found = window.find(attr)
            if found >= 0:
                title = window[found + len(attr):window.find('"', found + len(attr))]
                if title:
                    break
        if not title:
            continue
        price = _PRICE_NEAR.search(window)
        listings.append(Listing(
            source="mercari",
            listing_id=item_id,
            title=_unescape(title).strip(),
            url=ITEM_URL.format(id=item_id),
            price=_int(price.group(1).replace(",", "").replace(".", "")) if price else 0,
            currency="JPY",
            requested_at=requested_at,
            detected_at=received_at,
        ))
    return listings


def _unescape(text: str) -> str:
    import html as _html

    return _html.unescape(text)


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _retry_after(response: httpx.Response) -> float:
    try:
        return max(1.0, float(response.headers.get("Retry-After", "")))
    except (TypeError, ValueError):
        return 5.0
