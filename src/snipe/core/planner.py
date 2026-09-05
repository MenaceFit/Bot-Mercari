"""Planificateur de requêtes : combien d'appels réseau, et lesquels.

Le problème
-----------
Le cahier des charges demande « FEW REMOTE REQUESTS + FAST LOCAL FILTERING » :
chercher « ナイキ » une fois, puis retrouver localement « division »,
« trail », « tokyo ». C'est la bonne intuition — le filtrage local coûte
environ 4 µs par annonce, contre 100 à 300 ms pour un aller-retour réseau.

Mais cette stratégie a une limite dure, et elle est silencieuse.

Une marketplace ne renvoie que les N annonces les plus récentes (120 chez
Mercari). Si elle en publie plus de N entre deux de nos scans, la page ne
remonte plus jusqu'à notre passage précédent : il y a un TROU, et les
annonces tombées dedans ne seront jamais vues. Sur une requête très large
comme « ナイキ », ce trou est permanent — et rien ne le signale, puisque la
page est pleine de résultats.

Une requête précise, elle, est filtrée par la marketplace : ses N places
couvrent des heures au lieu de quelques secondes, et le trou n'existe pas.

Le compromis
------------
Aucune des deux stratégies n'est bonne dans l'absolu : cela dépend du débit
de publication réel derrière chaque requête, qu'on ne peut pas deviner à
l'avance. Le planificateur commence donc par la version économe — regrouper —
et se rétracte dès que la mesure montre que ça coûte des annonces.

Deux signaux, tous deux mesurés à l'exécution :

* **trous** — la page ne remonte plus au scan précédent. Signal fort et
  suffisant à lui seul : la requête perd des annonces MAINTENANT.
* **rendement** — trouvailles par annonce examinée. Signal faible, qui dit
  que la requête gaspille du budget sans forcément rien perdre.

Le premier déclenche un dégroupage immédiat, le second après confirmation.
Un dégroupage n'est jamais annulé automatiquement : repasser en mode large
recommencerait à perdre des annonces pour économiser des requêtes, ce qui
est le mauvais côté du compromis pour un sniper.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .keywords import Keyword
from .normalizer import covers, normalize_text

log = logging.getLogger(__name__)

#: Nombre d'annonces observées avant que le rendement veuille dire quelque
#: chose. En dessous, un rendement nul peut n'être qu'un marché calme.
MIN_SAMPLE = 400
#: Rendement (trouvailles / annonces vues) sous lequel une requête large ne
#: paie plus son coût réseau.
MIN_YIELD = 0.005


@dataclass(slots=True)
class PlannedQuery:
    """Une recherche à envoyer réellement, et ce qu'elle sert à couvrir."""

    text: str
    keywords: tuple[str, ...]          # noms lisibles couverts
    priority: str = "medium"
    #: True quand cette requête regroupe plusieurs variantes de recherche.
    #: Seules celles-là peuvent être dégroupées : une requête déjà précise
    #: n'a pas de repli.
    grouped: bool = False

    @property
    def normalized(self) -> str:
        return normalize_text(self.text)


@dataclass
class QueryPlanner:
    """Construit et révise l'ensemble des requêtes envoyées à une source."""

    #: Requêtes retirées du regroupement, avec le motif. On ne les regroupe
    #: plus jamais tant que le planificateur vit.
    demoted: dict[str, str] = field(default_factory=dict)
    #: Mesures par texte de requête.
    items_seen: dict[str, int] = field(default_factory=dict)
    hits: dict[str, int] = field(default_factory=dict)
    gaps: dict[str, int] = field(default_factory=dict)
    #: Plafond de requêtes précises synthétisées par mot-clé dégroupé. Sans
    #: lui, un mot-clé à huit écritures alternatives multiplierait par huit
    #: le coût réseau de son propre dégroupage.
    max_forms: int = 4

    # ── Construction du plan ──────────────────────────────────────────────
    def plan(self, keywords: list[Keyword], source: str = "") -> list[PlannedQuery]:
        """Renvoie les requêtes à envoyer pour couvrir tous les mots-clés.

        Un mot-clé doit être couvert par AU MOINS une requête. C'est
        l'invariant du planificateur : perdre un mot-clé en route serait un
        échec silencieux, exactement ce qu'on cherche à éviter.
        """
        active = [
            kw for kw in keywords
            if kw.enabled and (not kw.sources or not source or source in kw.sources)
        ]
        if not active:
            return []

        # 1. Ce que chaque mot-clé demande réellement — en tenant compte des
        #    requêtes déjà dégroupées, remplacées par leurs formes précises.
        requested: dict[str, set[str]] = {}
        for keyword in active:
            for text in keyword.search:
                for query in self._forms_for(keyword, text):
                    requested.setdefault(query, set()).add(keyword.display_name)

        # 2. Regroupement : une requête couverte par une autre, plus large et
        #    non dégroupée, n'a pas besoin d'être envoyée. C'est l'économie
        #    recherchée par « FEW REMOTE REQUESTS ».
        chosen: dict[str, set[str]] = {}
        for text in sorted(requested, key=lambda t: (len(normalize_text(t)), t)):
            broader = self._broader_among(text, chosen)
            if broader is not None and broader != text:
                chosen[broader].update(requested[text])
            else:
                chosen.setdefault(text, set()).update(requested[text])

        queries = [
            PlannedQuery(
                text=text,
                keywords=tuple(sorted(names)),
                priority=self._priority_of(names, active),
                grouped=len(names) > 1,
            )
            for text, names in chosen.items()
        ]

        covered = {name for query in queries for name in query.keywords}
        missing = [kw.display_name for kw in active if kw.display_name not in covered]
        if missing:
            # Ne devrait pas arriver ; si ça arrive, mieux vaut une requête
            # de trop qu'un mot-clé jamais interrogé.
            log.warning("mots-clés non couverts, requêtes ajoutées : %s", missing)
            queries.extend(
                PlannedQuery(text=name, keywords=(name,), priority="medium")
                for name in missing
            )
        return sorted(queries, key=lambda q: q.text)

    def _forms_for(self, keyword: Keyword, text: str) -> list[str]:
        """Les requêtes à envoyer pour ce (mot-clé, variante de recherche).

        Tant que la variante se comporte bien, c'est elle qu'on envoie : une
        seule requête large sert plusieurs mots-clés.

        Une fois dégroupée, on ne peut pas se contenter de la renvoyer telle
        quelle — ce serait ne rien changer. On SYNTHÉTISE des requêtes
        précises en combinant la variante avec les termes que le mot-clé
        exige : « ナイキ » + « ディビジョン » → « ナイキ ディビジョン ».
        La marketplace filtre alors elle-même, ses N places de résultats
        deviennent toutes pertinentes, et le trou disparaît.

        Une requête par alternative d'écriture, pas une seule : « ナイキ
        ディビジョン » ne trouverait pas un titre écrit « NIKE DIVISION ».
        C'est le coût assumé de l'exhaustivité — et c'est précisément le
        compromis que le dégroupage vient d'arbitrer en sa faveur.
        """
        if not self._is_demoted(text):
            return [text]
        if not keyword.include_groups:
            # Rien de plus précis à proposer : le mot-clé lui-même fait
            # office de requête, faute de mieux.
            return [keyword.display_name] if keyword.display_name else [text]

        forms = []
        for group in keyword.include_groups[: self.max_forms]:
            combined = f"{text} {' '.join(group)}".strip()
            if combined not in forms:
                forms.append(combined)
        if len(keyword.include_groups) > self.max_forms:
            log.warning(
                "« %s » a %d écritures alternatives, seules les %d premières "
                "deviennent des requêtes précises — les autres restent "
                "couvertes par le filtrage local uniquement",
                keyword.display_name, len(keyword.include_groups), self.max_forms,
            )
        return forms or [text]

    def _broader_among(self, text: str, chosen: dict[str, set[str]]) -> str | None:
        """La requête déjà retenue qui englobe `text`, s'il en existe une."""
        if text in chosen:
            return text
        for candidate in chosen:
            if covers(candidate, text) and not self._is_demoted(candidate):
                return candidate
        return None

    def _is_demoted(self, text: str) -> bool:
        return normalize_text(text) in self.demoted

    @staticmethod
    def _priority_of(names: set[str], keywords: list[Keyword]) -> str:
        """Une requête hérite de la priorité la PLUS haute qu'elle sert.

        Regrouper un mot-clé urgent avec un mot-clé tranquille ne doit pas
        ralentir l'urgent : sinon le regroupement ferait perdre la réactivité
        qu'il était censé préserver.
        """
        order = {"high": 0, "medium": 1, "low": 2}
        best = min(
            (order.get(kw.priority, 1) for kw in keywords if kw.display_name in names),
            default=1,
        )
        return ("high", "medium", "low")[best]

    # ── Retours d'exécution ───────────────────────────────────────────────
    def record_page(self, text: str, items: int, hits: int) -> None:
        key = normalize_text(text)
        self.items_seen[key] = self.items_seen.get(key, 0) + items
        self.hits[key] = self.hits.get(key, 0) + hits

    def record_gap(self, text: str) -> bool:
        """Signale qu'une page n'a pas remonté jusqu'au scan précédent.

        Renvoie True si cela vient de faire changer le plan.
        """
        key = normalize_text(text)
        self.gaps[key] = self.gaps.get(key, 0) + 1
        if key in self.demoted:
            return False
        # Un seul trou suffit. Contrairement au rendement, ce n'est pas une
        # statistique à confirmer : c'est la preuve que des annonces passent
        # à travers en ce moment même.
        self.demoted[key] = "trou : la page ne remonte plus au scan précédent"
        log.warning(
            "requête « %s » dégroupée — %s. Elle est remplacée par des "
            "requêtes précises, plus coûteuses mais exhaustives.",
            text, self.demoted[key],
        )
        return True

    def review_yield(self) -> list[str]:
        """Dégroupe les requêtes qui gaspillent le budget. Renvoie les textes."""
        changed = []
        for key, seen in self.items_seen.items():
            if key in self.demoted or seen < MIN_SAMPLE:
                continue
            ratio = self.hits.get(key, 0) / seen
            if ratio < MIN_YIELD:
                self.demoted[key] = (
                    f"rendement {ratio * 100:.2f} % sur {seen} annonces examinées"
                )
                changed.append(key)
                log.warning(
                    "requête « %s » dégroupée — %s. Le budget réseau part dans "
                    "des annonces qui ne peuvent pas correspondre.",
                    key, self.demoted[key],
                )
        return changed

    def yield_of(self, text: str) -> float:
        key = normalize_text(text)
        seen = self.items_seen.get(key, 0)
        return self.hits.get(key, 0) / seen if seen else 0.0

    def report(self) -> dict:
        return {
            "demoted": dict(self.demoted),
            "queries": {
                key: {
                    "items_seen": seen,
                    "hits": self.hits.get(key, 0),
                    "yield": round(self.yield_of(key), 5),
                    "gaps": self.gaps.get(key, 0),
                }
                for key, seen in sorted(self.items_seen.items())
            },
        }
