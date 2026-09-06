"""Adapter Buyee générique : une marketplace = un namespace d'URL.

Ce que j'ai pu établir sur Buyee, et comment
--------------------------------------------
`buyee.jp` est **inaccessible** depuis l'environnement où ce code a été
écrit : la passerelle réseau répond 403 au CONNECT. Aucune page n'a pu être
chargée. L'audit s'est donc fait par l'index des moteurs de recherche, qui
renvoie des URL réelles indexées — c'est une preuve de STRUCTURE, pas de
contenu.

URL réellement observées dans l'index (voir BUYEE_AUDIT.md) :

    https://buyee.jp/item/search/query/photocards?lang=en
    https://buyee.jp/item/search/query/{kw}/category/{id}?lang=en
    https://buyee.jp/item/search?query={kw}&category={id}&lang=en
    https://buyee.jp/item/search/advanced/yahoo/auction
    https://buyee.jp/mercari/search?seller=488413427&lang=en
    https://www.buyee.jp/mercari/search?seller=968116560&page=2
    https://buyee.jp/rakuma/?lang=en
    https://buyee.jp/paypayfleamarket/?lang=en
    https://shop.buyee.jp/{partenaire}/shopping/search/category/{id}?page=5

D'où la cartographie des namespaces (voir `MARKETPLACES` plus bas).

Ce que je n'ai PAS pu établir
-----------------------------
Le **balisage HTML** des pages de résultats. Aucun. Écrire
`node.css_first("li.itemCard")` sans avoir vu la page produirait du code
d'apparence crédible et faux — et un scraper faux ne lève pas d'erreur, il
renvoie zéro annonce, en silence. C'est le pire mode de panne possible.

D'où la conception : **l'extraction est une donnée, pas du code**. Les
sélecteurs vivent dans `radar.yaml`, et la commande

    buyee-radar calibrate

les découvre automatiquement sur la machine de l'utilisateur, où buyee.jp
est joignable. Tant qu'ils ne sont pas calibrés, l'adapter se déclare
`NEEDS_SELECTORS` et le dashboard l'affiche comme tel — jamais comme
« en ligne » alors qu'il ne ramène rien.

Respect des plateformes
-----------------------
Uniquement des pages publiques, en GET, avec un User-Agent honnête, une
concurrence bornée par adapter et un respect strict des `Retry-After`.
Aucun contournement de CAPTCHA, d'authentification ou de protection
anti-bot — et si une page en renvoie un, l'adapter s'arrête et le signale
au lieu d'insister.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlencode, urljoin

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

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_DIGITS_RE = re.compile(r"[\d,\.]+")
#: « 1.200 » et « 1,200 » valent mille deux cents. Un séparateur suivi
#: d'exactement trois chiffres est un séparateur de milliers.
_THOUSANDS_RE = re.compile(r"[.,](?=\d{3}(?:\D|$))")
#: Signatures d'une page de protection. On s'arrête, on n'insiste pas.
_CHALLENGE_MARKERS = (
    "captcha", "are you a human", "アクセスが制限", "unusual traffic",
    "cf-challenge", "recaptcha",
)


@dataclass(slots=True)
class Marketplace:
    """Un namespace Buyee, avec ce qu'on sait de lui."""

    source: str
    label: str
    #: Gabarit d'URL de recherche. `{keyword}` est encodé, `{page}` optionnel.
    search_url: str
    base_url: str = "https://buyee.jp/"
    #: Regex extrayant l'identifiant d'annonce depuis son URL.
    id_from_url: str = ""
    #: Paramètre de tri par date décroissante, s'il est connu.
    sort_newest: dict[str, str] = field(default_factory=dict)
    support: SupportLevel = SupportLevel.NEEDS_SELECTORS
    support_note: str = ""


#: Cartographie établie sur les URL indexées. Les chemins sont des preuves ;
#: les paramètres de tri sont des hypothèses raisonnables, marquées comme
#: telles et surchargeables dans radar.yaml.
MARKETPLACES: dict[str, Marketplace] = {
    "mercari": Marketplace(
        source="mercari",
        label="Mercari",
        search_url="https://buyee.jp/mercari/search?keyword={keyword}&page={page}",
        id_from_url=r"/mercari/item/([A-Za-z0-9_-]+)",
        sort_newest={"sort": "created_time", "order": "desc"},
        support_note=(
            "Namespace /mercari/search confirmé par des URL indexées "
            "(paramètres seller= et page=). Le nom du paramètre de mot-clé "
            "et le tri par date restent à confirmer sur une page réelle."
        ),
    ),
    "rakuma": Marketplace(
        source="rakuma",
        label="Rakuma",
        search_url="https://buyee.jp/rakuma/search?keyword={keyword}&page={page}",
        id_from_url=r"/rakuma/item/([A-Za-z0-9_-]+)",
        support_note=(
            "Namespace /rakuma/ confirmé (page d'accueil indexée). Le chemin "
            "de recherche est déduit par symétrie avec /mercari/search."
        ),
    ),
    "jdi_auction": Marketplace(
        source="jdi_auction",
        label="JDirectItems Auction",
        # Le seul namespace dont la forme de recherche est directement
        # attestée par plusieurs URL indexées.
        search_url="https://buyee.jp/item/search/query/{keyword}?page={page}&lang=en",
        id_from_url=r"/item/jdirectitems/auction/([A-Za-z0-9]+)",
        sort_newest={"sort": "end", "order": "desc"},
        support=SupportLevel.URL_VERIFIED,
        support_note=(
            "Forme /item/search/query/{mot-clé} attestée par de nombreuses "
            "URL indexées, avec variantes /category/{id} et ?query=. C'est "
            "le namespace des enchères (ex-Yahoo! Auctions)."
        ),
    ),
    "jdi_fleamarket": Marketplace(
        source="jdi_fleamarket",
        label="JDirectItems Fleamarket",
        search_url=(
            "https://buyee.jp/paypayfleamarket/search?keyword={keyword}&page={page}"
        ),
        id_from_url=r"/paypayfleamarket/item/([A-Za-z0-9_-]+)",
        support_note=(
            "Namespace /paypayfleamarket/ confirmé (page d'accueil indexée). "
            "Le chemin de recherche est déduit par symétrie."
        ),
    ),
    "jdi_shopping": Marketplace(
        source="jdi_shopping",
        label="JDirectItems Shopping",
        # Sous-domaine DIFFÉRENT, et la recherche y est par partenaire et
        # par catégorie, pas par mot-clé libre à l'échelle du site.
        search_url="https://shop.buyee.jp/search?keyword={keyword}&page={page}",
        base_url="https://shop.buyee.jp/",
        support=SupportLevel.UNSUPPORTED,
        support_note=(
            "shop.buyee.jp est un catalogue de boutiques partenaires "
            "(bookoff, az-style…), organisé par boutique et par catégorie : "
            "les URL indexées sont toutes de la forme "
            "/{partenaire}/shopping/search/category/{id}. Aucune recherche "
            "par mot-clé à l'échelle du site n'a pu être établie, et un "
            "catalogue de boutique n'est pas un flux de nouveautés — le "
            "surveiller pour du sniping n'aurait pas de sens. Active plutôt "
            "les marketplaces C2C, où les annonces apparaissent en continu."
        ),
    ),
    "rakuten": Marketplace(
        source="rakuten",
        label="Rakuten",
        search_url="https://buyee.jp/rakuten/search?keyword={keyword}&page={page}",
        support=SupportLevel.UNSUPPORTED,
        support_note=(
            "Rakuten est un catalogue de marchands : les fiches produit sont "
            "durables et réapprovisionnées, il n'y a pas de flux de nouvelles "
            "annonces à surveiller. Aucun namespace /rakuten/search n'a par "
            "ailleurs été trouvé dans l'index."
        ),
    ),
}


@dataclass(slots=True)
class Selectors:
    """Comment lire une page de résultats. Vient du YAML ou de la calibration.

    `item` et `title` sont indispensables. Sans identifiant stable, la
    déduplication renotifierait la même annonce à chaque scan : on refuse
    aussi de démarrer dans ce cas.
    """

    item: str = ""
    title: str = ""
    link: str = ""
    price: str = ""
    image: str = ""
    seller: str = ""
    time: str = ""
    next_page: str = ""

    @property
    def calibrated(self) -> bool:
        return bool(self.item and self.title)

    def to_dict(self) -> dict[str, str]:
        # `slots=True` supprime __dict__ : on passe par les champs déclarés.
        from dataclasses import fields
        return {
            f.name: getattr(self, f.name)
            for f in fields(self) if getattr(self, f.name)
        }


class BuyeeAdapter:
    """Lit une marketplace Buyee via ses pages publiques de résultats."""

    def __init__(
        self,
        marketplace: Marketplace,
        *,
        selectors: Selectors | None = None,
        max_concurrent: int = 4,
        connect_timeout: float = 5.0,
        read_timeout: float = 10.0,
        total_timeout: float = 15.0,
        max_retries: int = 2,
        search_url: str = "",
        extra_params: dict[str, str] | None = None,
    ) -> None:
        self.market = marketplace
        self.source = marketplace.source
        self.label = marketplace.label
        self.selectors = selectors or Selectors()
        self.search_url = search_url or marketplace.search_url
        self.extra_params = dict(extra_params or {})
        self.max_retries = max_retries
        self._max_concurrent = max_concurrent
        self._timeout = httpx.Timeout(
            total_timeout, connect=connect_timeout, read=read_timeout
        )
        self._client: httpx.AsyncClient | None = None
        self._gate: asyncio.Semaphore | None = None

    # ── Niveau de support ─────────────────────────────────────────────────
    @property
    def support(self) -> SupportLevel:
        """Le niveau réel, pas celui déclaré : il dépend de la calibration."""
        if self.market.support is SupportLevel.UNSUPPORTED:
            return SupportLevel.UNSUPPORTED
        if not self.selectors.calibrated:
            return SupportLevel.NEEDS_SELECTORS
        return self.market.support

    @property
    def support_note(self) -> str:
        if self.support is SupportLevel.NEEDS_SELECTORS:
            return (
                f"{self.market.support_note} Sélecteurs non calibrés : lance "
                f"« buyee-radar calibrate --source {self.source} » sur ta "
                f"machine, où buyee.jp est joignable."
            )
        return self.market.support_note

    # ── Cycle de vie ──────────────────────────────────────────────────────
    async def start(self) -> None:
        if self._client is None:
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
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
                },
            )
            self._gate = asyncio.Semaphore(self._max_concurrent)

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ── Construction de l'URL ─────────────────────────────────────────────
    def build_url(self, query: SearchQuery, page: int = 1) -> str:
        if query.cursor:
            return query.cursor
        url = self.search_url.replace("{keyword}", quote(query.text, safe=""))
        url = url.replace("{page}", str(page))

        params = dict(self.extra_params)
        params.update(self.market.sort_newest)
        if query.min_price:
            params.setdefault("price_min", str(query.min_price))
        if query.max_price:
            params.setdefault("price_max", str(query.max_price))
        if query.category:
            params.setdefault("category", query.category)
        if params:
            url += ("&" if "?" in url else "?") + urlencode(params)
        return url

    # ── Recherche ─────────────────────────────────────────────────────────
    async def search(self, query: SearchQuery) -> SearchResult:
        """Ne lève jamais pour une erreur attendue : renvoie `ok=False`.

        C'est le §32 du cahier des charges : un changement de format ne doit
        pas faire tomber l'application, et l'erreur doit rester visible dans
        les logs, le dashboard et la santé de la source.
        """
        if self.support is SupportLevel.UNSUPPORTED:
            return SearchResult(
                source=self.source, ok=False,
                error=f"source non supportée — {self.market.support_note}",
            )
        if not self.selectors.calibrated:
            return SearchResult(
                source=self.source, ok=False,
                error="sélecteurs non calibrés (buyee-radar calibrate)",
            )

        await self.start()
        assert self._client is not None and self._gate is not None
        url = self.build_url(query)

        async with self._gate:
            for attempt in range(self.max_retries + 1):
                requested_at = time.time()
                try:
                    response = await self._client.get(url)
                except httpx.HTTPError as exc:
                    if attempt < self.max_retries:
                        await asyncio.sleep(0.3 * (2 ** attempt))
                        continue
                    return SearchResult(
                        source=self.source, ok=False, error=f"réseau : {exc}",
                        requested_at=requested_at, received_at=time.time(),
                    )

                received_at = time.time()

                if response.status_code == 429:
                    raise AdapterError(
                        f"{self.label} : limite de débit (429)",
                        status=429, retry_after=_retry_after(response),
                    )
                if response.status_code in (401, 403, 451):
                    return SearchResult(
                        source=self.source, ok=False,
                        error=f"accès refusé ({response.status_code})",
                        requested_at=requested_at, received_at=received_at,
                    )
                if response.status_code >= 500 and attempt < self.max_retries:
                    await asyncio.sleep(0.3 * (2 ** attempt))
                    continue
                if response.status_code != 200:
                    return SearchResult(
                        source=self.source, ok=False,
                        error=f"HTTP {response.status_code}",
                        requested_at=requested_at, received_at=received_at,
                    )

                body = response.text
                marker = _challenge_marker(body)
                if marker:
                    # On ne tente RIEN pour passer outre : on le signale et on
                    # laisse le circuit breaker espacer la source.
                    return SearchResult(
                        source=self.source, ok=False,
                        error=(
                            f"page de protection détectée ({marker}) — l'adapter "
                            f"s'arrête, ralentis la cadence de cette source"
                        ),
                        requested_at=requested_at, received_at=received_at,
                    )

                return self.parse(body, requested_at, received_at, url)

        return SearchResult(source=self.source, ok=False, error="échec inattendu")

    async def fetch_latest(self, query: SearchQuery) -> SearchResult:
        """Page 1 triée par nouveauté. C'est le mode du monitoring temps réel."""
        return await self.search(query)

    # ── Extraction ────────────────────────────────────────────────────────
    def parse(
        self, html: str, requested_at: float, received_at: float, page_url: str = ""
    ) -> SearchResult:
        """Testable sans réseau, sur du HTML de fixture."""
        from selectolax.parser import HTMLParser

        tree = HTMLParser(html)
        sel = self.selectors
        base = self.market.base_url or page_url
        listings: list[Listing] = []

        for node in tree.css(sel.item):
            try:
                listing = self._listing(node, base, requested_at, received_at)
            except Exception:
                log.debug("%s : annonce ignorée", self.source, exc_info=True)
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
            # 200 + zéro annonce est presque toujours un sélecteur périmé,
            # pas un marché vide. Le dire fort évite des heures de « pourquoi
            # il ne trouve rien ».
            log.warning(
                "%s : aucune annonce extraite — sélecteurs probablement "
                "périmés (item=%r). Relance « buyee-radar calibrate ».",
                self.source, sel.item,
            )

        return SearchResult(
            source=self.source, listings=listings, ok=True, cursor=cursor,
            requested_at=requested_at, received_at=received_at,
        )

    def _listing(
        self, node: Any, base: str, requested_at: float, received_at: float
    ) -> Listing | None:
        sel = self.selectors

        title = _text(node.css_first(sel.title)) if sel.title else _text(node)
        if not title:
            return None

        link = node.css_first(sel.link) if sel.link else node.css_first("a")
        href = (link.attributes.get("href") or "") if link is not None else ""
        url = urljoin(base, href) if href else ""

        listing_id = ""
        if self.market.id_from_url and url:
            match = re.search(self.market.id_from_url, url)
            if match:
                listing_id = match.group(1)
        if not listing_id and url:
            listing_id = _fallback_id(url)
        if not listing_id:
            return None

        return Listing(
            source=self.source,
            listing_id=listing_id,
            title=title,
            url=url,
            price=_price(_text(node.css_first(sel.price)) if sel.price else ""),
            currency="JPY",
            image_url=_image(node.css_first(sel.image), base) if sel.image else "",
            seller=_text(node.css_first(sel.seller)) if sel.seller else "",
            # Les pages de liste n'affichent presque jamais l'heure exacte de
            # publication. `created_at` reste donc inconnu, et la latence de
            # détection ne sera PAS calculée plutôt que d'être inventée.
            created_at=_timestamp(_text(node.css_first(sel.time)) if sel.time else ""),
            requested_at=requested_at,
            detected_at=received_at,
        )

    # ── Santé ─────────────────────────────────────────────────────────────
    async def health_check(self) -> AdapterHealth:
        if self.support is SupportLevel.UNSUPPORTED:
            return AdapterHealth(
                source=self.source, ok=False, support=self.support,
                detail=self.market.support_note[:160],
            )
        if not self.selectors.calibrated:
            return AdapterHealth(
                source=self.source, ok=False, support=self.support,
                detail="sélecteurs non calibrés",
            )

        started = time.perf_counter()
        result = await self.search(SearchQuery(text="nike", limit=1))
        elapsed = round((time.perf_counter() - started) * 1000)
        if not result.ok:
            return AdapterHealth(
                source=self.source, ok=False, detail=result.error,
                latency_ms=elapsed, support=self.support,
            )
        if not len(result):
            return AdapterHealth(
                source=self.source, ok=False, latency_ms=elapsed,
                support=self.support,
                detail="page reçue mais aucune annonce extraite — recalibre",
            )
        return AdapterHealth(
            source=self.source, ok=True, latency_ms=elapsed,
            support=self.support, detail=f"{len(result)} annonce(s)",
        )


# ── Utilitaires d'extraction ──────────────────────────────────────────────
#: Derniers segments de chemin qui ne sont PAS des identifiants. Sur
#: Mandarake, l'annonce vit à « /order/detailPage/item?itemCode=123 » : le
#: dernier segment vaut « item » pour TOUTES les annonces. S'en servir
#: comme identifiant les ferait toutes passer pour la même, et la
#: déduplication n'en garderait qu'une — panne silencieuse, la pire.
_GENERIC_SEGMENTS = frozenset({
    "item", "items", "detail", "detailpage", "product", "products",
    "list", "listpage", "page", "view", "show", "index", "search",
})


def _fallback_id(url: str) -> str:
    """Identifiant de repli, quand aucun motif de source n'a fonctionné.

    Le dernier segment du chemin suffit pour une URL en « /item/{id} ». Il
    ne suffit pas quand l'identifiant est dans la query string : on
    retombe alors sur une empreinte de l'URL complète, qui a la seule
    propriété qui compte ici — être stable d'un scan à l'autre.
    """
    segment = url.rstrip("/").rsplit("/", 1)[-1].split("?")[0].split("#")[0]
    if segment and segment.lower() not in _GENERIC_SEGMENTS:
        return segment
    if not url:
        return ""
    from hashlib import blake2s
    return "u" + blake2s(url.encode("utf-8"), digest_size=8).hexdigest()


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
    for key in ("data-src", "data-original", "data-lazy", "src"):
        value = attrs.get(key)
        if value and not value.startswith("data:"):
            return urljoin(base, value)
    return ""


def _price(text: str) -> int:
    """« 8,900円 », « ¥12 500 », « 1.200 yen » → un entier."""
    if not text:
        return 0
    cleaned = text.replace(" ", "").replace(" ", "")
    match = _DIGITS_RE.search(cleaned)
    if not match:
        return 0
    try:
        return max(0, int(float(_THOUSANDS_RE.sub("", match.group(0)))))
    except ValueError:
        return 0


_REL_TIME_RE = re.compile(r"(\d+)\s*(秒|分|時間|日|second|minute|hour|day)")
_REL_UNITS = {
    "秒": 1, "second": 1,
    "分": 60, "minute": 60,
    "時間": 3600, "hour": 3600,
    "日": 86400, "day": 86400,
}


def _timestamp(text: str) -> float:
    """« 3分前 » / « 2 hours ago » → epoch. 0.0 si illisible.

    Les listes japonaises datent en relatif. Renvoyer 0 quand on ne sait pas
    lire est délibéré : la latence de détection ne sera alors pas calculée,
    plutôt que de l'être sur une valeur inventée.
    """
    if not text:
        return 0.0
    match = _REL_TIME_RE.search(text)
    if not match:
        return 0.0
    amount, unit = match.groups()
    factor = _REL_UNITS.get(unit.lower())
    if factor is None:
        return 0.0
    try:
        return time.time() - int(amount) * factor
    except ValueError:
        return 0.0


def _challenge_marker(body: str) -> str:
    lowered = body[:4000].lower()
    for marker in _CHALLENGE_MARKERS:
        if marker in lowered:
            return marker
    return ""


def _retry_after(response: httpx.Response) -> float:
    raw = response.headers.get("Retry-After")
    try:
        return max(0.5, float(raw)) if raw else 5.0
    except ValueError:
        return 5.0
