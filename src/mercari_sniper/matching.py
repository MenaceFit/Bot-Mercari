"""Normalisation de texte, matching de keywords et scoring de rareté.

C'est ici que vit la logique métier reprise du bot d'origine : un keyword
matche si *tous* ses mots sont présents dans le titre normalisé.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

# ── Paliers de rareté (repris et étendus depuis le bot v1) ────────────────────
ULTRA_TERMS = [
    "tokyo", "東京", "tokio", "gyakusou", "ギャクソウ", "berlin", "london",
    "paris", "nathan bell", "ネイサン", "トウキョウ", "ベルリン", "ロンドン", "パリ",
]
RARE_TERMS = [
    "trail", "トレイル", "acg", "phenom", "フェノム", "aeroloft", "エアロロフト",
    "windrunner", "ウィンドランナー", "wild run", "ワイルドラン", "division",
    "ディビジョン", "future fast", "aerovest", "project rock", "coldgear",
    "ハイブリッド",
]

ULTRA = ("ULTRA RARE", 0xE8334A)
RARE = ("RARE", 0xF5A623)
PREMIUM = ("PREMIUM", 0x00D4AA)

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")


@lru_cache(maxsize=8192)
def normalize(text: str) -> str:
    """NFKC + minuscules + ponctuation écrasée.

    NFKC est indispensable ici : il unifie les katakana demi-chasse
    (ﾅｲｷ) et pleine chasse (ナイキ), très courants dans les titres Mercari.
    Le cache évite de renormaliser les mêmes titres à chaque tick.
    """
    text = unicodedata.normalize("NFKC", text).lower()
    text = _PUNCT_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text).strip()


def rarity_of(title: str, keyword: str = "") -> tuple[str, int]:
    """Renvoie (libellé, couleur) — même hiérarchie que le bot d'origine."""
    haystack = normalize(f"{title} {keyword}")
    if any(term in haystack for term in ULTRA_TERMS):
        return ULTRA
    if any(term in haystack for term in RARE_TERMS):
        return RARE
    return PREMIUM


@dataclass(slots=True)
class Rule:
    """Un keyword compilé, avec ses filtres optionnels.

    `terms` sont pré-normalisés une fois au chargement : le hot path
    (des milliers de titres/minute) ne fait plus que des `in`.
    """

    keyword: str
    terms: tuple[str, ...] = ()
    exclude_terms: tuple[str, ...] = ()
    min_price: int | None = None
    max_price: int | None = None
    enabled: bool = True

    @classmethod
    def compile(
        cls,
        keyword: str,
        *,
        exclude: str = "",
        min_price: int | None = None,
        max_price: int | None = None,
        enabled: bool = True,
    ) -> "Rule":
        return cls(
            keyword=keyword,
            terms=tuple(normalize(keyword).split()),
            exclude_terms=tuple(normalize(exclude).split()) if exclude else (),
            min_price=min_price,
            max_price=max_price,
            enabled=enabled,
        )

    def matches(self, normalized_title: str, price: int) -> bool:
        if not self.enabled or not self.terms:
            return False
        if not all(term in normalized_title for term in self.terms):
            return False
        if any(term in normalized_title for term in self.exclude_terms):
            return False
        if self.min_price is not None and price < self.min_price:
            return False
        if self.max_price is not None and price > self.max_price:
            return False
        return True


@dataclass
class Matcher:
    """Applique l'ensemble des règles à un titre.

    Un index inversé sur le mot le plus rare de chaque règle permet de
    n'évaluer qu'une poignée de règles par titre au lieu des 60+.
    """

    rules: list[Rule] = field(default_factory=list)
    _index: dict[str, list[Rule]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.reindex()

    def reindex(self) -> None:
        """(Re)construit l'index inversé. À rappeler après toute modif des règles."""
        freq: dict[str, int] = {}
        for rule in self.rules:
            for term in rule.terms:
                freq[term] = freq.get(term, 0) + 1

        index: dict[str, list[Rule]] = {}
        for rule in self.rules:
            if not rule.terms:
                continue
            # Le terme le moins partagé discrimine le mieux.
            pivot = min(rule.terms, key=lambda t: freq[t])
            index.setdefault(pivot, []).append(rule)
        self._index = index

    def match(self, title: str, price: int) -> list[str]:
        """Renvoie les keywords touchés par ce titre, sans doublon et ordonnés."""
        normalized = normalize(title)
        if not normalized:
            return []

        seen: set[str] = set()
        hits: list[str] = []
        # On ne teste que les règles dont le pivot est présent dans le titre.
        for pivot, candidates in self._index.items():
            if pivot not in normalized:
                continue
            for rule in candidates:
                if rule.keyword in seen:
                    continue
                if rule.matches(normalized, price):
                    seen.add(rule.keyword)
                    hits.append(rule.keyword)
        return hits
