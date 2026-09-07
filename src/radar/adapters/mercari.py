"""Mercari Japon — client de l'API de recherche officielle.

C'est la seule source du projet, et la seule qui ait jamais ramené de
vraies annonces. Pourquoi elle, et pourquoi comme ça :

* **L'API, pas le HTML.** `api.mercari.jp/v2/entities:search` renvoie du
  JSON structuré. Pas de sélecteurs CSS à deviner, pas de page qui change
  de forme un matin sans prévenir.
* **Tri par date de publication.** `SORT_CREATED_TIME` + `ORDER_DESC` :
  les annonces les plus récentes arrivent en tête. C'est exactement ce
  qu'il faut pour du sniping — pas une approximation.
* **Un vrai horodatage.** Chaque annonce porte son champ `created`. La
  latence affichée est donc mesurée, pas estimée.

Authentification
----------------
Mercari exige un jeton **DPoP** : un JWT signé en ES256 dont la clé
publique voyage dans l'en-tête. Aucun compte, aucun mot de passe, aucun
cookie — la paire de clés est générée au démarrage et vit en mémoire. Si
le serveur rejette le jeton (401/403), on régénère l'identité et on
retente une fois : c'est ce que fait le site lui-même.

Ce qu'on ne fait pas : aucun contournement de limite de débit. Un 429 est
respecté, `Retry-After` compris, et le disjoncteur prend le relais.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

import httpx

from .base import (
    AdapterError,
    AdapterHealth,
    Listing,
    SearchQuery,
    SearchResult,
    SupportLevel,
)
from .dpop import DPoPSigner

log = logging.getLogger(__name__)

SEARCH_URL = "https://api.mercari.jp/v2/entities:search"
ITEM_URL = "https://jp.mercari.com/item/{id}"
SHOP_URL = "https://jp.mercari.com/shops/product/{id}"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

#: Le corps attendu par l'API. Les champs vides ne sont pas décoratifs :
#: l'endpoint refuse la requête s'ils manquent.
_CONDITION: dict[str, Any] = {
    "keyword": "", "excludeKeyword": "",
    "sort": "SORT_CREATED_TIME", "order": "ORDER_DESC",
    "status": ["STATUS_ON_SALE"],
    "sizeId": [], "categoryId": [], "brandId": [], "sellerId": [],
    "priceMin": 0, "priceMax": 0,
    "itemConditionId": [], "shippingPayerId": [], "shippingFromArea": [],
    "shippingMethod": [], "colorId": [], "hasCoupon": False,
    "attributes": [], "itemTypes": [], "skuIds": [], "shopIds": [],
    "excludeShippingMethodIds": [],
}

_BODY: dict[str, Any] = {
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


class MercariAdapter:
    """Recherche Mercari Japon, triée par date de publication."""

    source = "mercari"
    label = "Mercari Japon"
    support = SupportLevel.VERIFIED
    support_note = "API de recherche officielle, triée par date de publication."

    def __init__(
        self,
        *,
        max_concurrent: int = 6,
        connect_timeout: float = 5.0,
        read_timeout: float = 10.0,
        total_timeout: float = 15.0,
        max_retries: int = 2,
        proxy: str | None = None,
    ) -> None:
        self.max_retries = max(1, max_retries)
        self._max_concurrent = max(1, max_concurrent)
        self._timeout = httpx.Timeout(
            total_timeout, connect=connect_timeout, read=read_timeout
        )
        self._proxy = proxy
        self._signer = DPoPSigner()
        self._session_id = uuid.uuid4().hex
        self._client: httpx.AsyncClient | None = None
        self._gate: asyncio.Semaphore | None = None

    # ── Cycle de vie ──────────────────────────────────────────────────────
    async def start(self) -> None:
        if self._client is not None:
            return
        self._client = httpx.AsyncClient(
            http2=True,
            timeout=self._timeout,
            limits=httpx.Limits(
                max_connections=self._max_concurrent,
                max_keepalive_connections=self._max_concurrent,
                keepalive_expiry=90.0,
            ),
            headers={
                "User-Agent": _UA,
                "Accept": "*/*",
                "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
                "Content-Type": "application/json; charset=utf-8",
                "Origin": "https://jp.mercari.com",
                "Referer": "https://jp.mercari.com/",
                "X-Platform": "web",
            },
            proxy=self._proxy,
        )
        self._gate = asyncio.Semaphore(self._max_concurrent)

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ── Recherche ─────────────────────────────────────────────────────────
    def _payload(self, query: SearchQuery) -> dict[str, Any]:
        condition = dict(_CONDITION)
        condition["keyword"] = query.text
        if query.min_price:
            condition["priceMin"] = int(query.min_price)
        if query.max_price:
            condition["priceMax"] = int(query.max_price)
        if query.category:
            condition["categoryId"] = [query.category]

        body = dict(_BODY)
        body["pageSize"] = max(1, min(120, query.limit))
        body["pageToken"] = query.cursor
        body["searchSessionId"] = self._session_id
        body["searchCondition"] = condition
        return body

    async def search(self, query: SearchQuery) -> SearchResult:
        """Ne lève jamais pour un échec attendu : renvoie `ok=False`.

        Une source qui tombe ne doit pas arrêter le scanner, et l'erreur
        doit rester visible dans le dashboard.
        """
        await self.start()
        assert self._client is not None and self._gate is not None
        body = self._payload(query)

        async with self._gate:
            for attempt in range(self.max_retries):
                requested_at = time.time()
                try:
                    response = await self._client.post(
                        SEARCH_URL, json=body,
                        headers={"DPoP": self._signer.token("POST", SEARCH_URL)},
                    )
                except httpx.HTTPError as exc:
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(0.25 * (attempt + 1))
                        continue
                    return SearchResult(
                        source=self.source, ok=False, error=f"réseau : {exc}",
                        requested_at=requested_at, received_at=time.time(),
                    )

                received_at = time.time()
                code = response.status_code

                if code == 200:
                    try:
                        return self._parse(
                            response.json(), requested_at, received_at
                        )
                    except ValueError as exc:
                        return SearchResult(
                            source=self.source, ok=False,
                            error=f"réponse illisible : {exc}",
                            requested_at=requested_at, received_at=received_at,
                        )

                if code == 429:
                    # On ne contourne pas : on remonte le délai demandé et
                    # c'est l'ordonnanceur qui ralentit.
                    raise AdapterError(
                        "limite de débit Mercari (429)",
                        status=429, retry_after=_retry_after(response),
                    )

                if code in (401, 403) and attempt < self.max_retries - 1:
                    # Jeton refusé : on régénère l'identité, comme le site.
                    log.debug("DPoP rejeté (%s) — nouvelle identité", code)
                    self._signer = DPoPSigner()
                    self._session_id = uuid.uuid4().hex
                    continue

                return SearchResult(
                    source=self.source, ok=False,
                    error=f"HTTP {code} : {response.text[:160]}",
                    requested_at=requested_at, received_at=received_at,
                )

        return SearchResult(source=self.source, ok=False, error="épuisé")

    async def fetch_latest(self, query: SearchQuery) -> SearchResult:
        return await self.search(query)

    # ── Lecture de la réponse ─────────────────────────────────────────────
    def _parse(
        self, payload: dict, requested_at: float, received_at: float
    ) -> SearchResult:
        listings: list[Listing] = []
        for raw in payload.get("items") or []:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            try:
                listings.append(self._listing(raw, requested_at, received_at))
            except Exception:      # noqa: BLE001
                # Une annonce malformée ne doit jamais coûter la page
                # entière : l'API évolue, et perdre une ligne vaut mieux
                # que perdre les cinquante-neuf autres.
                log.debug("annonce ignorée (parsing)", exc_info=True)

        meta = payload.get("meta") or {}
        return SearchResult(
            source=self.source,
            listings=listings,
            cursor=str(
                payload.get("nextPageToken") or meta.get("nextPageToken") or ""
            ),
            requested_at=requested_at,
            received_at=received_at,
        )

    def _listing(
        self, raw: dict[str, Any], requested_at: float, received_at: float
    ) -> Listing:
        item_id = str(raw.get("id") or "")
        thumbs = raw.get("thumbnails") or []
        # Les annonces de particuliers ont un id « m<chiffres> » ; les
        # boutiques Mercari Shops utilisent une autre route.
        template = (
            ITEM_URL if item_id.startswith("m") and item_id[1:].isdigit()
            else SHOP_URL
        )
        brand = raw.get("itemBrand")
        return Listing(
            source=self.source,
            listing_id=item_id,
            title=str(raw.get("name") or ""),
            url=template.format(id=item_id),
            price=_int(raw.get("price")),
            currency="JPY",
            image_url=str(thumbs[0]) if thumbs else "",
            seller=str(raw.get("sellerId") or ""),
            category=str(raw.get("categoryId") or ""),
            condition=str(raw.get("itemConditionId") or ""),
            brand=str(brand.get("name") or "") if isinstance(brand, dict) else "",
            # Vrai horodatage de publication : la latence est mesurée, pas
            # estimée.
            created_at=float(_int(raw.get("created"))),
            updated_at=float(_int(raw.get("updated"))),
            requested_at=requested_at,
            detected_at=received_at,
        )

    # ── Santé ─────────────────────────────────────────────────────────────
    async def health_check(self) -> AdapterHealth:
        started = time.perf_counter()
        result = await self.search(SearchQuery(text="nike", limit=1))
        elapsed = round((time.perf_counter() - started) * 1000)
        return AdapterHealth(
            source=self.source,
            ok=result.ok,
            detail=result.error or f"{len(result.listings)} annonce(s)",
            latency_ms=elapsed,
            support=self.support,
        )


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _retry_after(response: httpx.Response) -> float:
    raw = response.headers.get("Retry-After", "")
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        return 5.0
