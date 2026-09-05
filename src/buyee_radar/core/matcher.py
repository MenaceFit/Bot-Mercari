"""Moteur de filtrage local.

Tout se joue ici en microsecondes : une annonce reçue coûte environ 4 µs à
filtrer, contre 100 à 300 ms pour l'aller-retour réseau qui l'a ramenée. Le
filtrage n'est donc jamais le goulet — mais il doit le rester, d'où l'index
inversé et l'absence totale d'allocation dans `match()`.

L'ordre des tests n'est pas arbitraire : les exclusions globales passent
AVANT les inclusions, et les inclusions avant les bornes de prix. On écarte
au plus tôt et au moins cher.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..adapters.base import Listing
from .keywords import Keyword
from .normalizer import contains_any, normalize_text


@dataclass(slots=True)
class GlobalFilters:
    """Filtres appliqués à toutes les annonces, quel que soit le mot-clé."""

    min_price: int | None = None
    max_price: int | None = None
    exclude_terms: tuple[str, ...] = ()
    exclude_regex: tuple[re.Pattern, ...] = ()
    allowed_sources: tuple[str, ...] = ()
    allowed_conditions: tuple[str, ...] = ()
    #: Une annonce publiée il y a plus de ça n'est pas une nouveauté. Garde-fou
    #: pour le premier scan après un redémarrage : sans lui, tout le catalogue
    #: déjà en ligne partirait en notification.
    max_age_seconds: int = 900

    @classmethod
    def build(
        cls,
        *,
        min_price: int | None = None,
        max_price: int | None = None,
        exclude: list[str] | None = None,
        exclude_regex: list[str] | None = None,
        sources: list[str] | None = None,
        conditions: list[str] | None = None,
        max_age_seconds: int = 900,
    ) -> "GlobalFilters":
        compiled = []
        for raw in exclude_regex or []:
            try:
                compiled.append(re.compile(raw, re.IGNORECASE | re.UNICODE))
            except re.error:
                continue
        return cls(
            min_price=min_price,
            max_price=max_price,
            exclude_terms=tuple(
                normalized for term in (exclude or [])
                if (normalized := normalize_text(term))
            ),
            exclude_regex=tuple(compiled),
            allowed_sources=tuple(sources or ()),
            allowed_conditions=tuple(conditions or ()),
            max_age_seconds=max_age_seconds,
        )


#: Motifs de rejet, tels qu'ils remontent dans les métriques. Un filtre
#: silencieux est indiscernable d'un marché calme : chaque rejet est compté.
REASONS = ("exclu", "prix", "source", "état", "trop ancienne", "aucun mot-clé")


@dataclass
class FilterEngine:
    """Applique les filtres globaux puis les mots-clés à chaque annonce."""

    keywords: list[Keyword] = field(default_factory=list)
    globals_: GlobalFilters = field(default_factory=GlobalFilters)
    drops: dict[str, int] = field(default_factory=lambda: {r: 0 for r in REASONS})
    _index: dict[str, list[Keyword]] = field(default_factory=dict, repr=False)
    _unindexed: list[Keyword] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self.reindex()

    def reindex(self) -> None:
        """Index inversé sur le terme le plus discriminant de chaque mot-clé.

        Sans lui, chaque titre serait confronté aux N mots-clés. Avec, on
        n'évalue que ceux dont le terme pivot est déjà présent dans le titre.
        Les mots-clés sans pivot exploitable (aucune inclusion, nom vide)
        sont évalués systématiquement — ils sont rares.
        """
        index: dict[str, list[Keyword]] = {}
        unindexed: list[Keyword] = []
        for keyword in self.keywords:
            if not keyword.enabled:
                continue
            pivot = keyword.pivot
            if pivot:
                index.setdefault(pivot, []).append(keyword)
            else:
                unindexed.append(keyword)
        self._index = index
        self._unindexed = unindexed

    def set_keywords(self, keywords: list[Keyword]) -> None:
        self.keywords = keywords
        self.reindex()

    # ── Chemin critique ───────────────────────────────────────────────────
    def match(
        self,
        listing: Listing,
        now: float = 0.0,
        allowed: frozenset[str] | None = None,
    ) -> list[str]:
        """Noms des mots-clés touchés. Liste vide = l'annonce est écartée.

        `allowed` restreint l'évaluation aux mots-clés que la requête ayant
        ramené cette annonce sert réellement. Sans cette restriction, un
        mot-clé dont les inclusions ne mentionnent pas la marque contamine
        les autres : « Nike Tokyo » (include: 東京) matchait une annonce
        « アンダーアーマー 東京 » ramenée par la requête Under Armour. Le
        symptôme est traître — les notifications ont l'air plausibles, mais
        la marque est fausse.

        Le prix de cette restriction est un cas rare : une annonce citant
        deux marques ne sera rattachée qu'aux mots-clés de la requête qui
        l'a ramenée. C'est le bon côté du compromis — une notification juste
        vaut mieux que deux dont une fausse.

        Incrémente le compteur de rejet correspondant : c'est ce qui rend un
        filtre trop strict visible au lieu de le laisser supprimer des
        annonces en silence.
        """
        g = self.globals_

        if g.allowed_sources and listing.source not in g.allowed_sources:
            self.drops["source"] += 1
            return []
        if g.allowed_conditions and listing.condition not in g.allowed_conditions:
            self.drops["état"] += 1
            return []
        if g.min_price is not None and listing.price < g.min_price:
            self.drops["prix"] += 1
            return []
        if g.max_price is not None and listing.price > g.max_price:
            self.drops["prix"] += 1
            return []
        if (
            g.max_age_seconds
            and listing.created_at
            and now
            and (now - listing.created_at) > g.max_age_seconds
        ):
            self.drops["trop ancienne"] += 1
            return []

        title = normalize_text(listing.title)
        if not title:
            self.drops["aucun mot-clé"] += 1
            return []

        if g.exclude_terms and contains_any(title, g.exclude_terms):
            self.drops["exclu"] += 1
            return []
        for pattern in g.exclude_regex:
            if pattern.search(title):
                self.drops["exclu"] += 1
                return []

        hits: list[str] = []
        seen: set[str] = set()
        for pivot, candidates in self._index.items():
            if pivot not in title:
                continue
            for keyword in candidates:
                if keyword.display_name in seen:
                    continue
                if allowed is not None and keyword.display_name not in allowed:
                    continue
                if keyword.matches(title, listing.price, listing.source):
                    seen.add(keyword.display_name)
                    hits.append(keyword.display_name)
        for keyword in self._unindexed:
            if keyword.display_name in seen:
                continue
            if allowed is not None and keyword.display_name not in allowed:
                continue
            if keyword.matches(title, listing.price, listing.source):
                seen.add(keyword.display_name)
                hits.append(keyword.display_name)

        if not hits:
            self.drops["aucun mot-clé"] += 1
        return hits

    def report(self) -> dict[str, int]:
        return dict(self.drops)
