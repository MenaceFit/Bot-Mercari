"""Backend direct sur l'API de recherche Mercari v2.

Pourquoi ne pas passer par `mercapi` : cette lib crée un client HTTP par
instance, ne réutilise pas les connexions entre scans et ajoute une couche
de désérialisation dataclass coûteuse. Ici on garde un seul client HTTP/2
chaud pour toute la durée de vie du process — c'est le gain de latence
principal (pas de handshake TLS à chaque tick).
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid

import httpx

from ..dpop import DPoPSigner
from ..models import Listing
from .base import BackendError, SearchPage, SearchQuery

log = logging.getLogger(__name__)

SEARCH_URL = "https://api.mercari.jp/v2/entities:search"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Champs constants du corps de requête : construits une fois, copiés par requête.
_BASE_CONDITION = {
    "excludeKeyword": "",
    "sort": "SORT_CREATED_TIME",
    "order": "ORDER_DESC",
    "status": ["STATUS_ON_SALE"],
    "sizeId": [],
    "categoryId": [],
    "brandId": [],
    "sellerId": [],
    "priceMin": 0,
    "priceMax": 0,
    "itemConditionId": [],
    "shippingPayerId": [],
    "shippingFromArea": [],
    "shippingMethod": [],
    "colorId": [],
    "hasCoupon": False,
    "attributes": [],
    "itemTypes": [],
    "skuIds": [],
    "shopIds": [],
    "excludeShippingMethodIds": [],
}

_BASE_BODY = {
    "userId": "",
    "pageToken": "",
    "indexRouting": "INDEX_ROUTING_UNSPECIFIED",
    "thumbnailTypes": [],
    "defaultDatasets": [],
    "serviceFrom": "suruga",
    "withItemBrand": True,
    "withItemSize": False,
    "withItemPromotions": False,
    "withItemSizes": False,
    "withShopname": False,
    "useDynamicAttribute": False,
    "withSuggestedItems": False,
    "withOfferPricePromotion": False,
    "withProductSuggest": False,
    "withParentProducts": False,
    "withProductArticles": False,
    "withSearchConditionId": False,
}


class MercariAPIBackend:
    """Client HTTP/2 persistant vers l'API Mercari."""

    name = "mercari-api"

    def __init__(
        self,
        *,
        timeout: float = 8.0,
        max_retries: int = 3,
        max_connections: int = 32,
        proxy: str | None = None,
    ) -> None:
        self._signer = DPoPSigner()
        self._max_retries = max_retries
        self._search_session_id = uuid.uuid4().hex
        self._client = httpx.AsyncClient(
            http2=True,
            timeout=httpx.Timeout(timeout, connect=5.0),
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=max_connections,
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

    # ── Construction de la requête ────────────────────────────────────────
    def _build_body(self, query: SearchQuery) -> dict:
        condition = dict(_BASE_CONDITION)
        condition["keyword"] = query.keyword
        condition["excludeKeyword"] = query.exclude_keyword
        if query.min_price:
            condition["priceMin"] = int(query.min_price)
        if query.max_price:
            condition["priceMax"] = int(query.max_price)
        if query.categories:
            condition["categoryId"] = list(query.categories)
        if query.brands:
            condition["brandId"] = list(query.brands)
        if query.item_conditions:
            condition["itemConditionId"] = list(query.item_conditions)

        body = dict(_BASE_BODY)
        body["pageSize"] = max(1, min(120, query.page_size))
        body["pageToken"] = query.page_token
        body["searchSessionId"] = self._search_session_id
        body["searchCondition"] = condition
        return body

    # ── Recherche ─────────────────────────────────────────────────────────
    async def search(self, query: SearchQuery) -> SearchPage:
        body = self._build_body(query)
        last_error: Exception | None = None

        for attempt in range(self._max_retries):
            try:
                response = await self._client.post(
                    SEARCH_URL,
                    json=body,
                    headers={"DPoP": self._signer.token("POST", SEARCH_URL)},
                )
            except httpx.HTTPError as exc:
                last_error = exc
                # Erreur transport : on retente avec un backoff jitteré.
                if attempt < self._max_retries - 1:
                    await asyncio.sleep((2**attempt) * 0.25 * (1 + random.random()))
                    continue
                raise BackendError(f"échec réseau: {exc}") from exc

            if response.status_code == 200:
                return self._parse(response.json(), query.keyword)

            if response.status_code == 429:
                retry_after = _parse_retry_after(response)
                raise BackendError(
                    "rate limit Mercari (429)",
                    status=429,
                    retry_after=retry_after,
                )

            if response.status_code in (401, 403):
                # Jeton DPoP rejeté : on régénère l'identité et on retente une fois.
                if attempt < self._max_retries - 1:
                    log.warning(
                        "HTTP %s — rotation de la clé DPoP", response.status_code
                    )
                    self._signer.rotate()
                    self._search_session_id = uuid.uuid4().hex
                    await asyncio.sleep(0.5 * (1 + random.random()))
                    continue
                raise BackendError(
                    f"accès refusé ({response.status_code})",
                    status=response.status_code,
                )

            if 500 <= response.status_code < 600 and attempt < self._max_retries - 1:
                await asyncio.sleep((2**attempt) * 0.25 * (1 + random.random()))
                continue

            raise BackendError(
                f"HTTP {response.status_code}: {response.text[:200]}",
                status=response.status_code,
            )

        raise BackendError(f"échec après {self._max_retries} tentatives: {last_error}")

    @staticmethod
    def _parse(payload: dict, keyword: str) -> SearchPage:
        items = payload.get("items") or []
        listings: list[Listing] = []
        for raw in items:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            try:
                listings.append(Listing.from_api(raw, source=keyword))
            except Exception:  # une annonce malformée ne casse pas le scan
                log.debug("annonce ignorée (parsing): %r", raw, exc_info=True)

        meta = payload.get("meta") or {}
        next_token = str(
            payload.get("nextPageToken") or meta.get("nextPageToken") or ""
        )
        return SearchPage(items=listings, next_page_token=next_token)

    async def aclose(self) -> None:
        await self._client.aclose()


def _parse_retry_after(response: httpx.Response) -> float:
    raw = response.headers.get("Retry-After")
    if not raw:
        return 5.0
    try:
        return max(0.5, float(raw))
    except ValueError:
        return 5.0
