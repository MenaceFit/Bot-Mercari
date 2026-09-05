"""Source Mercari Japon, via son API de recherche v2.

Choix techniques et leur raison
-------------------------------
* **Un seul client HTTP/2, gardé chaud pour toute la session.** C'est le gain
  de latence principal : sans lui, chaque scan repaie un handshake TLS
  (typiquement 100-200 ms depuis l'Europe vers le Japon). Le pooling et le
  keep-alive sont explicitement configurés, pas laissés au hasard.
* **Signature DPoP maison.** L'API exige un JWT ES256 dont la clé publique
  est dans l'en-tête. Une bibliothèque JWT généraliste produirait une
  signature DER ; l'API veut du R||S brut.
* **Trois timeouts distincts** (connexion, lecture, total) : une requête ne
  doit jamais pouvoir bloquer un worker indéfiniment.
* **`excludeKeyword` côté serveur** : le bruit écarté par Mercari ne consomme
  ni bande passante ni — surtout — de place dans la page de résultats. Une
  page de 120 remplie de parfums, c'est 120 annonces utiles en moins.

État de vérification
--------------------
Le client est porté d'une version en service, mais `api.mercari.jp` est
inaccessible depuis l'environnement de développement (403 au CONNECT de la
passerelle réseau). Le chemin réseau n'a donc PAS pu être exercé ici : le
parsing est testé sur des réponses capturées, la signature DPoP est vérifiée
structurellement.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from typing import Any

import httpx

from .base import Listing, SearchQuery, SearchResult, SourceError, SourceHealth
from .dpop import DPoPSigner

log = logging.getLogger(__name__)

SEARCH_URL = "https://api.mercari.jp/v2/entities:search"
ITEM_URL = "https://jp.mercari.com/item/{id}"
SHOP_URL = "https://jp.mercari.com/shops/product/{id}"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_BASE_CONDITION: dict[str, Any] = {
    "excludeKeyword": "",
    "sort": "SORT_CREATED_TIME",
    "order": "ORDER_DESC",
    "status": ["STATUS_ON_SALE"],
    "sizeId": [], "categoryId": [], "brandId": [], "sellerId": [],
    "priceMin": 0, "priceMax": 0,
    "itemConditionId": [], "shippingPayerId": [], "shippingFromArea": [],
    "shippingMethod": [], "colorId": [], "hasCoupon": False,
    "attributes": [], "itemTypes": [], "skuIds": [], "shopIds": [],
    "excludeShippingMethodIds": [],
}

_BASE_BODY: dict[str, Any] = {
    "userId": "", "pageToken": "",
    "indexRouting": "INDEX_ROUTING_UNSPECIFIED",
    "thumbnailTypes": [], "defaultDatasets": [],
    "serviceFrom": "suruga",
    "withItemBrand": True, "withItemSize": False, "withItemPromotions": False,
    "withItemSizes": False, "withShopname": False, "useDynamicAttribute": False,
    "withSuggestedItems": False, "withOfferPricePromotion": False,
    "withProductSuggest": False, "withParentProducts": False,
    "withProductArticles": False, "withSearchConditionId": False,
}


class MercariSource:
    """Client HTTP/2 persistant sur l'API de recherche Mercari."""

    name = "mercari"
    verified = False   # chemin réseau non exerçable depuis l'environnement de dev

    def __init__(
        self,
        *,
        connect_timeout: float = 5.0,
        read_timeout: float = 8.0,
        total_timeout: float = 12.0,
        max_concurrent: int = 8,
        max_retries: int = 3,
        proxy: str | None = None,
    ) -> None:
        self._signer = DPoPSigner()
        self._session_id = uuid.uuid4().hex
        self._max_retries = max_retries
        # Le sémaphore borne la concurrence PAR SOURCE. Sans lui, une rafale
        # de tâches échues ouvrirait autant de connexions simultanées, ce qui
        # déclenche des 429 sans rien accélérer.
        self._gate = asyncio.Semaphore(max_concurrent)
        self._client = httpx.AsyncClient(
            http2=True,
            timeout=httpx.Timeout(
                total_timeout, connect=connect_timeout, read=read_timeout
            ),
            limits=httpx.Limits(
                max_connections=max_concurrent,
                max_keepalive_connections=max_concurrent,
                keepalive_expiry=90.0,
            ),
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "*/*",
                "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
                "Content-Type": "application/json; charset=utf-8",
                "Origin": "https://jp.mercari.com",
                "Referer": "https://jp.mercari.com/",
                "X-Platform": "web",
            },
            proxy=proxy,
        )

    # ── Requête ───────────────────────────────────────────────────────────
    def _body(self, query: SearchQuery) -> dict[str, Any]:
        condition = dict(_BASE_CONDITION)
        condition["keyword"] = query.text
        condition["excludeKeyword"] = query.exclude_text
        if query.min_price:
            condition["priceMin"] = int(query.min_price)
        if query.max_price:
            condition["priceMax"] = int(query.max_price)
        if query.categories:
            condition["categoryId"] = list(query.categories)

        body = dict(_BASE_BODY)
        body["pageSize"] = max(1, min(120, query.limit))
        body["pageToken"] = query.cursor
        body["searchSessionId"] = self._session_id
        body["searchCondition"] = condition
        return body

    async def search(self, query: SearchQuery) -> SearchResult:
        body = self._body(query)
        last: Exception | None = None

        async with self._gate:
            for attempt in range(self._max_retries):
                requested_at = time.time()
                try:
                    response = await self._client.post(
                        SEARCH_URL,
                        json=body,
                        headers={"DPoP": self._signer.token("POST", SEARCH_URL)},
                    )
                except httpx.HTTPError as exc:
                    last = exc
                    if attempt < self._max_retries - 1:
                        await self._backoff(attempt)
                        continue
                    raise SourceError(f"réseau : {exc}") from exc

                received_at = time.time()

                if response.status_code == 200:
                    return self._parse(response.json(), requested_at, received_at)

                if response.status_code == 429:
                    raise SourceError(
                        "limite de débit Mercari (429)",
                        status=429,
                        retry_after=_retry_after(response),
                    )

                if response.status_code in (401, 403):
                    # Jeton DPoP rejeté : on régénère l'identité et on retente.
                    if attempt < self._max_retries - 1:
                        log.warning("HTTP %s — rotation DPoP", response.status_code)
                        self._signer.rotate()
                        self._session_id = uuid.uuid4().hex
                        await asyncio.sleep(0.5 * (1 + random.random()))
                        continue
                    raise SourceError(
                        f"accès refusé ({response.status_code})",
                        status=response.status_code,
                        transient=False,
                    )

                if 500 <= response.status_code < 600 and attempt < self._max_retries - 1:
                    await self._backoff(attempt)
                    continue

                raise SourceError(
                    f"HTTP {response.status_code}: {response.text[:200]}",
                    status=response.status_code,
                )

        raise SourceError(f"échec après {self._max_retries} tentatives : {last}")

    @staticmethod
    async def _backoff(attempt: int) -> None:
        await asyncio.sleep((2 ** attempt) * 0.25 * (1 + random.random()))

    # ── Parsing ───────────────────────────────────────────────────────────
    def _parse(
        self, payload: dict, requested_at: float, received_at: float
    ) -> SearchResult:
        listings: list[Listing] = []
        for raw in payload.get("items") or []:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            try:
                listings.append(self._listing(raw, requested_at, received_at))
            except Exception:
                # Une annonce malformée ne doit jamais faire tomber le scan :
                # l'API évolue, et perdre une annonce vaut mieux que perdre
                # la page entière.
                log.debug("annonce ignorée (parsing)", exc_info=True)

        meta = payload.get("meta") or {}
        return SearchResult(
            listings=listings,
            cursor=str(payload.get("nextPageToken") or meta.get("nextPageToken") or ""),
            requested_at=requested_at,
            received_at=received_at,
        )

    def _listing(
        self, raw: dict[str, Any], requested_at: float, received_at: float
    ) -> Listing:
        item_id = str(raw.get("id") or "")
        thumbs = raw.get("thumbnails") or []
        # Les annonces particuliers ont un id « m<chiffres> » ; les boutiques
        # Mercari Shops utilisent un autre format et une autre route.
        url = (
            ITEM_URL if item_id.startswith("m") and item_id[1:].isdigit() else SHOP_URL
        ).format(id=item_id)

        return Listing(
            id=item_id,
            source=self.name,
            title=str(raw.get("name") or ""),
            url=url,
            price=_int(raw.get("price")),
            currency="JPY",
            image_url=str(thumbs[0]) if thumbs else "",
            seller=str(raw.get("sellerId") or ""),
            category=str(raw.get("categoryId") or ""),
            condition=str(raw.get("itemConditionId") or ""),
            brand=str((raw.get("itemBrand") or {}).get("name") or "")
            if isinstance(raw.get("itemBrand"), dict) else "",
            published_at=float(_int(raw.get("created"))),
            updated_at=float(_int(raw.get("updated"))),
            requested_at=requested_at,
            received_at=received_at,
            raw=raw,
        )

    # ── Santé / arrêt ─────────────────────────────────────────────────────
    async def health_check(self) -> SourceHealth:
        started = time.perf_counter()
        try:
            result = await self.search(SearchQuery(text="nike", limit=1))
        except SourceError as exc:
            return SourceHealth(ok=False, detail=str(exc))
        elapsed = int((time.perf_counter() - started) * 1000)
        return SourceHealth(
            ok=True, detail=f"{len(result)} annonce(s)", latency_ms=elapsed
        )

    async def close(self) -> None:
        await self._client.aclose()


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _retry_after(response: httpx.Response) -> float:
    raw = response.headers.get("Retry-After")
    if not raw:
        return 5.0
    try:
        return max(0.5, float(raw))
    except ValueError:
        return 5.0
