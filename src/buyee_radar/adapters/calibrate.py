"""Découverte automatique des sélecteurs d'une page de résultats.

Le problème que ça résout
-------------------------
Les pages Buyee n'ont pas pu être chargées depuis l'environnement de
développement, et leur balisage change sans préavis de toute façon. Écrire
des sélecteurs en dur revient donc à livrer du code qui cessera de
fonctionner sans rien dire.

Plutôt que de deviner, on **apprend la structure sur une page réelle**, sur
la machine de l'utilisateur, où le site est joignable.

Comment
-------
Une page de résultats est une liste d'éléments quasi identiques. On cherche
donc la classe CSS qui :

1. apparaît au moins `min_items` fois (une liste, pas un encart) ;
2. dont chaque occurrence contient un lien ET un montant ;
3. dont les occurrences ont des contenus DIFFÉRENTS entre elles — un menu
   répété échouerait ce test, une liste d'annonces le passe ;
4. est la plus PROFONDE possible — sinon on capture le conteneur de la
   liste entière au lieu d'une annonce.

Les sous-sélecteurs (titre, prix, image…) sont ensuite cherchés à
l'intérieur d'une occurrence, ce qui est beaucoup plus fiable que de
raisonner sur la page entière.

Le résultat est proposé à l'utilisateur avec un **échantillon de ce qui a
été extrait**, pour qu'il vérifie d'un coup d'œil avant d'enregistrer. Une
calibration silencieuse serait aussi dangereuse qu'un sélecteur en dur.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .buyee_html import Selectors, _price, _text

log = logging.getLogger(__name__)

_PRICE_RE = re.compile(r"[\d,\.]{3,}\s*(円|yen|¥|JPY)", re.IGNORECASE)
#: Conteneurs de page, jamais des annonces. Les écarter d'emblée évite de
#: proposer `div.container` comme sélecteur d'article.
_LAYOUT_WORDS = (
    "wrapper", "container", "layout", "page", "header", "footer",
    "nav", "menu", "sidebar", "breadcrumb", "pagination",
)


@dataclass
class CalibrationResult:
    source: str
    ok: bool
    selectors: Selectors = field(default_factory=Selectors)
    items_found: int = 0
    #: Échantillon extrait, pour vérification humaine avant enregistrement.
    sample: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str = ""

    def render(self) -> str:
        lines = [f"\n  Calibration — {self.source}", "  " + "─" * 56]
        if not self.ok:
            lines += [f"  ÉCHEC : {self.error}", ""]
            lines += [f"  · {note}" for note in self.notes]
            return "\n".join(lines) + "\n"

        lines.append(f"  {self.items_found} annonces détectées sur la page\n")
        lines.append("  Sélecteurs découverts :")
        for name, value in self.selectors.to_dict().items():
            lines.append(f"    {name:12} {value}")

        lines.append("\n  Échantillon extrait — VÉRIFIE que c'est cohérent :")
        for item in self.sample:
            price = f"{item['price']:,}".replace(",", " ") if item["price"] else "?"
            lines.append(f"    {price:>9} ¥  {item['title'][:52]}")
            if item.get("url"):
                lines.append(f"               {item['url'][:70]}")
        for note in self.notes:
            lines.append(f"\n  ⚠ {note}")
        return "\n".join(lines) + "\n"


def calibrate(
    html: str, source: str = "?", *, min_items: int = 5, sample_size: int = 3
) -> CalibrationResult:
    """Déduit les sélecteurs d'une page de résultats déjà téléchargée."""
    from selectolax.parser import HTMLParser

    tree = HTMLParser(html)
    result = CalibrationResult(source=source, ok=False)

    candidate = _find_item_container(tree, min_items)
    if candidate is None:
        result.error = (
            "aucune structure répétée exploitable — la page est peut-être "
            "vide, rendue en JavaScript, ou protégée"
        )
        result.notes.append(
            "Vérifie que l'URL renvoie bien des résultats dans un navigateur, "
            "puis réessaie. Si la page se construit en JavaScript, ses "
            "annonces ne sont pas dans le HTML et cette source ne peut pas "
            "être surveillée ainsi."
        )
        return result

    selector, nodes = candidate
    first = nodes[0]

    selectors = Selectors(
        item=selector,
        title=_pick_title(first),
        link=_pick(first, ["a[href]"]),
        price=_pick_price(first),
        image=_pick(first, ["img"]),
        seller=_pick(first, [".seller", "[class*=seller]", "[class*=shop]"]),
        time=_pick_time(first),
        next_page=_pick_next_page(tree),
    )
    if not selectors.title:
        result.error = "titre introuvable dans les éléments répétés"
        return result

    # On rejoue l'extraction avec les sélecteurs trouvés : proposer une
    # calibration sans vérifier qu'elle produit quelque chose serait
    # exactement le défaut qu'on cherche à éviter.
    sample: list[dict[str, Any]] = []
    for node in nodes[:sample_size]:
        title_node = node.css_first(selectors.title)
        link_node = node.css_first(selectors.link) if selectors.link else None
        price_node = node.css_first(selectors.price) if selectors.price else None
        sample.append({
            "title": _text(title_node),
            "url": (link_node.attributes.get("href") or "") if link_node else "",
            "price": _price(_text(price_node)),
        })

    result.ok = bool(sample and sample[0]["title"])
    result.selectors = selectors
    result.items_found = len(nodes)
    result.sample = sample

    if not result.ok:
        result.error = "les sélecteurs trouvés n'extraient rien"
        return result
    if not selectors.price or not any(item["price"] for item in sample):
        result.notes.append(
            "aucun prix extrait — le filtre de prix et le scoring seront "
            "inopérants sur cette source"
        )
    if not selectors.time:
        result.notes.append(
            "aucune date de publication sur la page : la latence de détection "
            "ne sera pas calculable pour cette source (elle affichera « — » "
            "plutôt qu'un chiffre inventé)"
        )
    if not selectors.next_page:
        result.notes.append(
            "aucun lien « page suivante » : le rattrapage de trou sera limité "
            "à la première page"
        )
    return result


# ── Recherche du conteneur d'annonce ──────────────────────────────────────
def _find_item_container(tree: Any, min_items: int) -> tuple[str, list] | None:
    """La classe la plus profonde qui se répète en portant lien + prix."""
    counts: Counter[str] = Counter()
    for node in tree.css("li, div, article, section"):
        classes = (node.attributes.get("class") or "").split()
        tag = node.tag
        for cls in classes:
            if len(cls) < 3 or any(word in cls.lower() for word in _LAYOUT_WORDS):
                continue
            counts[f"{tag}.{cls}"] += 1

    best: tuple[str, list] | None = None
    best_depth = -1

    for selector, count in counts.most_common(60):
        if count < min_items:
            continue
        nodes = tree.css(selector)
        if len(nodes) < min_items:
            continue
        if not _looks_like_listings(nodes):
            continue
        # Plus le conteneur est profond, plus il est proche de l'annonce
        # elle-même. Sans ce critère on retiendrait la grille entière.
        depth = _depth(nodes[0])
        if depth > best_depth:
            best, best_depth = (selector, nodes), depth

    return best


def _looks_like_listings(nodes: list) -> bool:
    """Lien + prix dans la plupart des éléments, et contenus distincts."""
    sample = nodes[:12]
    with_link = sum(1 for n in sample if n.css_first("a[href]") is not None)
    with_price = sum(1 for n in sample if _PRICE_RE.search(n.text() or ""))
    if with_link < len(sample) * 0.7:
        return False
    if with_price < len(sample) * 0.5:
        return False
    # Un menu répété a le même texte partout ; une liste d'annonces non.
    texts = {(" ".join((n.text() or "").split()))[:80] for n in sample}
    return len(texts) >= max(2, len(sample) // 2)


def _depth(node: Any) -> int:
    depth = 0
    current = node.parent
    while current is not None and depth < 64:
        depth += 1
        current = current.parent
    return depth


# ── Sous-sélecteurs ───────────────────────────────────────────────────────
def _pick(node: Any, candidates: list[str]) -> str:
    for selector in candidates:
        try:
            if node.css_first(selector) is not None:
                return selector
        except Exception:
            continue
    return ""


def _pick_title(node: Any) -> str:
    """Le titre est le texte le plus long porté par un lien, ou un heading."""
    for selector in ("h1, h2, h3", "[class*=title]", "[class*=name]"):
        found = node.css_first(selector)
        if found is not None and _text(found):
            return selector

    best, best_len = "", 0
    for link in node.css("a[href]"):
        text = _text(link)
        cls = (link.attributes.get("class") or "").split()
        if len(text) > best_len and cls:
            best, best_len = f"a.{cls[0]}", len(text)
    if best:
        return best
    return "a[href]" if node.css_first("a[href]") is not None else ""


def _pick_price(node: Any) -> str:
    for selector in ("[class*=price]", "[class*=Price]", "[class*=yen]"):
        found = node.css_first(selector)
        if found is not None and _PRICE_RE.search(found.text() or ""):
            return selector
    # Repli : le plus petit élément dont le texte ressemble à un montant.
    for child in node.css("span, p, div, em, strong"):
        text = _text(child)
        if _PRICE_RE.fullmatch(text) or (_PRICE_RE.search(text) and len(text) < 24):
            classes = (child.attributes.get("class") or "").split()
            if classes:
                return f"{child.tag}.{classes[0]}"
    return ""


def _pick_time(node: Any) -> str:
    for selector in ("time", "[class*=time]", "[class*=date]", "[class*=ago]"):
        if node.css_first(selector) is not None:
            return selector
    return ""


def _pick_next_page(tree: Any) -> str:
    for selector in (
        "a[rel=next]", "a[class*=next]", "[class*=pagination] a[class*=next]",
        "[class*=pager] a[class*=next]",
    ):
        if tree.css_first(selector) is not None:
            return selector
    return ""
