"""Normalisation de texte et comparaison de termes.

Deux écritures cohabitent dans les titres Mercari, et elles n'obéissent pas
aux mêmes règles :

* **Japonais** — pas de séparateur de mots. « ナイキエアマックス » est un seul
  bloc ; chercher « ナイキ » dedans ne peut se faire qu'en sous-chaîne.
* **Latin** — les mots sont séparés. Une recherche en sous-chaîne y produit
  des faux positifs coûteux : « air » se trouve dans « repair » et
  « hair dryer », « acg » dans rien d'utile mais « pro » dans « produit ».

D'où deux régimes de comparaison, choisis automatiquement selon le terme.
L'ancrage latin est fait en DÉBUT de mot seulement : « nike » doit trouver
« nikelab », « air » doit trouver « airmax ». Un ancrage des deux côtés
raterait les composés, qui sont la norme sur ces marketplaces.

Toutes les regex sont compilées une fois et mémorisées : le chemin critique
traite des milliers de titres par minute.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")
# Un terme « latin » : uniquement lettres/chiffres ASCII et espaces. C'est le
# seul cas où la notion de frontière de mot a un sens.
_LATIN_RE = re.compile(r"^[a-z0-9]+(?: [a-z0-9]+)*$")


@lru_cache(maxsize=16384)
def normalize_text(text: str) -> str:
    """NFKC + minuscules + ponctuation écrasée + espaces normalisés.

    NFKC est indispensable : il unifie les katakana demi-chasse (ﾅｲｷ) et
    pleine chasse (ナイキ), ainsi que les chiffres pleine chasse (２７ → 27),
    tous très courants dans les titres japonais.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = _PUNCT_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text).strip()


@lru_cache(maxsize=8192)
def _matcher_for(term: str):
    """Compile le prédicat de recherche adapté à l'écriture du terme."""
    if _LATIN_RE.match(term):
        return re.compile(r"(?<![a-z0-9])" + re.escape(term)).search
    return lambda text, _t=term: _t in text


def contains_term(normalized_text: str, term: str) -> bool:
    """`term` (déjà normalisé) apparaît-il dans ce texte déjà normalisé ?"""
    if not term:
        return False
    return bool(_matcher_for(term)(normalized_text))


def contains_all(normalized_text: str, terms: tuple[str, ...]) -> bool:
    return all(contains_term(normalized_text, term) for term in terms)


def contains_any(normalized_text: str, terms: tuple[str, ...]) -> bool:
    return any(contains_term(normalized_text, term) for term in terms)


def terms_of(phrase: str) -> tuple[str, ...]:
    """Découpe une expression en termes normalisés.

    « Nike Division » → ('nike', 'division') : les deux doivent être présents.
    """
    return tuple(normalize_text(phrase).split())


def covers(broad: str, precise: str) -> bool:
    """Une recherche sur `broad` ramène-t-elle forcément les résultats de `precise` ?

    Chercher « ナイキ » ramène tout ce qui contient ナイキ, donc aussi
    « ナイキ ディビジョン ». La requête large couvre donc la précise si tous
    ses termes se retrouvent dans la précise.
    """
    broad_terms = terms_of(broad)
    if not broad_terms:
        return False
    return contains_all(normalize_text(precise), broad_terms)


def cache_stats() -> dict[str, int]:
    """Pour le benchmark : les caches font-ils leur travail ?"""
    info = normalize_text.cache_info()
    return {
        "normalize_hits": info.hits,
        "normalize_misses": info.misses,
        "matchers_compiled": _matcher_for.cache_info().currsize,
    }


def clear_caches() -> None:
    normalize_text.cache_clear()
    _matcher_for.cache_clear()
