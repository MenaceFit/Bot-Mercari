"""Scoring : à quel point une annonce mérite qu'on lâche ce qu'on fait.

Le score gouverne les notifications (§24 : `score >= 90` → Telegram +
Discord, 70-89 → dashboard + Discord, < 70 → dashboard seul). Il doit donc
être **explicable** : quand une annonce sort à 94, on doit pouvoir dire
pourquoi, sinon on ne peut ni régler les seuils ni faire confiance au tri.

Chaque contribution est donc conservée et renvoyée avec le score.

Ce que le score ne prétend PAS être
-----------------------------------
Une estimation de valeur marchande. Deux signaux du cahier des charges —
« historical demand » et « seller signals » — supposent un historique dont
on ne dispose pas au premier lancement. Ils sont donc calculés sur ce que
la base contient RÉELLEMENT (fréquence observée du mot-clé, rareté du
vendeur), et valent zéro tant qu'il n'y a pas assez de données. Un signal
inventé serait pire qu'un signal absent : il déplacerait les seuils sans
qu'on sache pourquoi.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..adapters.base import Listing
from .normalizer import contains_any, normalize_text

#: Paliers. Ils portent un LIBELLÉ, jamais une couleur seule — l'interface
#: affiche le texte à côté de la pastille.
TIERS = (
    (90, "ULTRA RARE"),
    (70, "VERY RARE"),
    (50, "RARE"),
    (0, "NORMAL"),
)


def tier_of(score: int) -> str:
    for threshold, label in TIERS:
        if score >= threshold:
            return label
    return "NORMAL"


#: Marques dont une pièce technique justifie une alerte. Configurable.
DEFAULT_PREMIUM_BRANDS = (
    "arcteryx", "アークテリクス", "veilance",
    "gyakusou", "ギャクソウ", "acg", "undercover", "アンダーカバー",
    "sacai", "サカイ", "visvim", "ビズビム", "nike acg",
)
#: Lignes rares au sein d'une marque grand public.
DEFAULT_RARE_LINES = (
    "division", "ディビジョン", "gyakusou", "ギャクソウ", "acg",
    "tokyo", "東京", "berlin", "ベルリン", "nathan bell", "ネイサン",
    "aeroloft", "エアロロフト", "windrunner", "ウィンドランナー",
    "trail", "トレイル", "veilance", "ヴェイランス",
)
#: Sources où une trouvaille est plus rare, donc plus précieuse. Mercari est
#: le plus fréquenté : y trouver quelque chose est moins remarquable.
DEFAULT_SOURCE_BONUS = {
    "mercari": 0,
    "rakuma": 6,
    "jdi_fleamarket": 8,
    "jdi_auction": 4,
}


@dataclass
class ScoringConfig:
    premium_brands: tuple[str, ...] = DEFAULT_PREMIUM_BRANDS
    rare_lines: tuple[str, ...] = DEFAULT_RARE_LINES
    source_bonus: dict[str, int] = field(
        default_factory=lambda: dict(DEFAULT_SOURCE_BONUS)
    )
    #: En dessous de ce prix, une pièce qui matche est probablement sous-évaluée.
    bargain_price: int = 6000
    #: Au-dessus, c'est probablement un lot ou une pièce de collection — ni
    #: l'un ni l'autre n'est ce qu'un sniper cherche en priorité.
    expensive_price: int = 60000
    #: Nombre d'observations avant que la fréquence d'un mot-clé veuille dire
    #: quelque chose. En dessous, le signal reste à zéro.
    demand_min_samples: int = 20


@dataclass
class ScoreBreakdown:
    """Le détail, pour que le score reste explicable."""

    total: int
    tier: str
    parts: dict[str, int] = field(default_factory=dict)

    def explain(self) -> str:
        ordered = sorted(self.parts.items(), key=lambda kv: -abs(kv[1]))
        return " · ".join(f"{name} {value:+d}" for name, value in ordered if value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.total,
            "tier": self.tier,
            "parts": dict(self.parts),
            "explain": self.explain(),
        }


class ScoringEngine:
    """Calcule un score 0-100 et le palier correspondant."""

    def __init__(self, config: ScoringConfig | None = None) -> None:
        self.config = config or ScoringConfig()
        #: Fréquence observée par mot-clé — la « demande historique » du
        #: cahier des charges, mesurée et non postulée.
        self._keyword_hits: dict[str, int] = {}
        self._total_hits = 0
        #: Vendeurs déjà croisés : un vendeur qui inonde le fil est moins
        #: intéressant qu'un particulier qui vend une pièce.
        self._seller_hits: dict[str, int] = {}

    # ── Apprentissage ─────────────────────────────────────────────────────
    def observe(self, listing: Listing) -> None:
        """Enregistre une trouvaille. Alimente les signaux statistiques."""
        for keyword in listing.keywords or ([listing.keyword] if listing.keyword else []):
            self._keyword_hits[keyword] = self._keyword_hits.get(keyword, 0) + 1
        self._total_hits += 1
        if listing.seller:
            self._seller_hits[listing.seller] = self._seller_hits.get(listing.seller, 0) + 1

    # ── Calcul ────────────────────────────────────────────────────────────
    def score(self, listing: Listing) -> ScoreBreakdown:
        cfg = self.config
        title = normalize_text(listing.title)
        parts: dict[str, int] = {}

        # Base : une annonce qui matche un mot-clé part de 40. En dessous,
        # tout serait « NORMAL » et le palier ne discriminerait rien.
        parts["correspondance"] = 40 if listing.keywords else 0

        # Correspondance exacte du nom du mot-clé dans le titre : le signal
        # le plus fort qui existe sans historique.
        for keyword in listing.keywords:
            if all(term in title for term in normalize_text(keyword).split()):
                parts["titre exact"] = 15
                break

        if contains_any(title, tuple(normalize_text(b) for b in cfg.premium_brands)):
            parts["marque premium"] = 14
        if contains_any(title, tuple(normalize_text(l) for l in cfg.rare_lines)):
            parts["ligne rare"] = 12

        bonus = cfg.source_bonus.get(listing.source, 0)
        if bonus:
            parts["source"] = bonus

        # Prix : une pièce qui matche ET qui est peu chère mérite d'être vue
        # vite. Un prix nul veut dire « non extrait », pas « gratuit ».
        if listing.price:
            if listing.price <= cfg.bargain_price:
                parts["prix bas"] = 10
            elif listing.price >= cfg.expensive_price:
                parts["prix élevé"] = -8

        parts["rareté observée"] = self._rarity_signal(listing)
        parts["vendeur"] = self._seller_signal(listing)

        total = max(0, min(100, sum(parts.values())))
        return ScoreBreakdown(total=total, tier=tier_of(total), parts=parts)

    def _rarity_signal(self, listing: Listing) -> int:
        """Un mot-clé rarement touché vaut plus qu'un mot-clé qui sort sans cesse.

        Reste à zéro tant qu'on n'a pas assez d'observations : une rareté
        calculée sur trois annonces ne voudrait rien dire.
        """
        if self._total_hits < self.config.demand_min_samples or not listing.keywords:
            return 0
        rate = min(
            self._keyword_hits.get(keyword, 0) / self._total_hits
            for keyword in listing.keywords
        )
        if rate <= 0.02:
            return 12
        if rate <= 0.08:
            return 6
        if rate >= 0.40:
            return -5      # ce mot-clé sature le fil
        return 0

    def _seller_signal(self, listing: Listing) -> int:
        if not listing.seller or self._total_hits < self.config.demand_min_samples:
            return 0
        seen = self._seller_hits.get(listing.seller, 0)
        if seen >= 10:
            return -6      # revendeur en volume
        if seen == 0:
            return 3       # jamais croisé
        return 0

    def stats(self) -> dict[str, Any]:
        return {
            "total_observed": self._total_hits,
            "keywords_tracked": len(self._keyword_hits),
            "sellers_tracked": len(self._seller_hits),
            "signals_active": self._total_hits >= self.config.demand_min_samples,
        }
