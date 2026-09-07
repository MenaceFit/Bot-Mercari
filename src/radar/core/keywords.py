"""Modèle de mot-clé : ce qu'on demande au réseau vs ce qu'on filtre en local.

Un mot-clé porte quatre choses distinctes, et les confondre est ce qui fait
soit rater des annonces, soit gaspiller le budget de requêtes :

* `display_name`   — le nom lisible, celui qui s'affiche dans la notification
* `search`         — les textes réellement envoyés à la marketplace
* `include`        — ce qui doit se trouver dans le titre (filtré en local)
* `exclude`        — ce qui disqualifie l'annonce (filtré en local)

L'intérêt est de découpler les deux : on peut chercher « ナイキ » une seule
fois et retrouver localement « division », « trail », « tokyo »… sans envoyer
une requête par variante.

C'est la bonne idée du cahier des charges, MAIS elle a une limite mesurée
dont le planificateur (`planner.py`) tient compte : une requête large ne
ramène que les N annonces les plus récentes. Si la marketplace en publie
plus que N entre deux scans, la page ne remonte plus jusqu'au passage
précédent et des annonces sont perdues — silencieusement. Le regroupement
n'est donc pas décidé une fois pour toutes ici, il est arbitré à l'exécution
sur des mesures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .normalizer import contains_all, contains_any, normalize_text, terms_of

#: Priorités admises, de la plus urgente à la plus lâche. Elles pilotent la
#: cadence de scan (voir `scheduler.py`).
PRIORITIES = ("high", "medium", "low")
DEFAULT_PRIORITY = "medium"


@dataclass(slots=True)
class Keyword:
    """Un mot-clé compilé, prêt pour le chemin critique.

    Tout ce qui peut être précalculé l'est au chargement : normalisation,
    découpage en termes, compilation des regex. `matches()` ne fait plus que
    des comparaisons.
    """

    display_name: str
    search: tuple[str, ...] = ()
    priority: str = DEFAULT_PRIORITY
    enabled: bool = True

    min_price: int | None = None
    max_price: int | None = None
    sources: tuple[str, ...] = ()          # vide = toutes les sources

    # Formes compilées
    include_groups: tuple[tuple[str, ...], ...] = ()
    exclude_terms: tuple[str, ...] = ()
    include_regex: tuple[re.Pattern, ...] = ()
    exclude_regex: tuple[re.Pattern, ...] = ()

    @classmethod
    def build(
        cls,
        name: str,
        *,
        search: list[str] | None = None,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        include_regex: list[str] | None = None,
        exclude_regex: list[str] | None = None,
        priority: str = DEFAULT_PRIORITY,
        min_price: int | None = None,
        max_price: int | None = None,
        sources: list[str] | None = None,
        enabled: bool = True,
    ) -> "Keyword":
        """Compile un mot-clé depuis sa forme YAML.

        `include` est une liste d'ALTERNATIVES : « ディビジョン » OU
        « division » — ce sont deux écritures de la même chose, exiger les
        deux ne matcherait jamais rien. Chaque alternative peut elle-même
        contenir plusieurs mots, tous requis (« nathan bell »).

        Sans `search`, le nom lisible sert de recherche : c'est le cas simple
        où l'utilisateur écrit juste « Nike Division ».
        """
        searches = tuple(dict.fromkeys(s.strip() for s in (search or []) if s.strip()))
        if not searches:
            searches = (name.strip(),)

        groups = tuple(
            terms for phrase in (include or [])
            if (terms := terms_of(phrase))
        )
        excludes = tuple(dict.fromkeys(
            term for phrase in (exclude or [])
            for term in terms_of(phrase)
        ))

        return cls(
            display_name=name.strip(),
            search=searches,
            priority=priority if priority in PRIORITIES else DEFAULT_PRIORITY,
            enabled=enabled,
            min_price=min_price,
            max_price=max_price,
            sources=tuple(sources or ()),
            include_groups=groups,
            exclude_terms=excludes,
            include_regex=_compile_all(include_regex),
            exclude_regex=_compile_all(exclude_regex),
        )

    # ── Chemin critique ───────────────────────────────────────────────────
    def matches(self, normalized_title: str, price: int = 0, source: str = "") -> bool:
        """Ce titre correspond-il ? Aucune allocation, aucune compilation."""
        if not self.enabled:
            return False
        if self.sources and source and source not in self.sources:
            return False

        # L'exclusion d'abord : c'est le test le plus discriminant et le
        # moins cher, et il évite d'évaluer les inclusions pour rien.
        if self.exclude_terms and contains_any(normalized_title, self.exclude_terms):
            return False
        for pattern in self.exclude_regex:
            if pattern.search(normalized_title):
                return False

        if self.include_groups and not any(
            contains_all(normalized_title, group) for group in self.include_groups
        ):
            return False
        for pattern in self.include_regex:
            if not pattern.search(normalized_title):
                return False

        if self.min_price is not None and price < self.min_price:
            return False
        if self.max_price is not None and price > self.max_price:
            return False
        return True

    @property
    def pivot(self) -> str:
        """Terme le plus discriminant, pour l'index inversé du filtre."""
        if self.include_groups:
            # Le plus long terme du premier groupe : le plus rare en pratique.
            return max(self.include_groups[0], key=len)
        terms = terms_of(self.display_name)
        return max(terms, key=len) if terms else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.display_name,
            "search": list(self.search),
            "include": [" ".join(g) for g in self.include_groups],
            "exclude": list(self.exclude_terms),
            "priority": self.priority,
            "enabled": self.enabled,
            "min_price": self.min_price,
            "max_price": self.max_price,
            "sources": list(self.sources),
        }


def _compile_all(patterns: list[str] | None) -> tuple[re.Pattern, ...]:
    """Compile les regex une fois. Une regex invalide est ignorée, pas fatale.

    Une faute de frappe dans le YAML ne doit pas empêcher le bot de démarrer :
    elle coûterait bien plus cher que le filtre qu'elle représente.
    """
    out = []
    for raw in patterns or []:
        try:
            out.append(re.compile(raw, re.IGNORECASE | re.UNICODE))
        except re.error:
            continue
    return tuple(out)


def normalize_for_match(title: str) -> str:
    """Point d'entrée unique de la normalisation côté filtre."""
    return normalize_text(title)
