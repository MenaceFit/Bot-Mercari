"""Vocabulaire de bruit : ce qu'on ne veut jamais voir remonter.

Pourquoi ce module existe
-------------------------
Un keyword d'une seule marque (« dior », « gucci ») matche *tout* le
catalogue de cette marque — donc aussi les parfums, le maquillage et les
échantillons, qui sont parmi les articles les plus publiés sur Mercari.
Le fil se remplit alors de bruit et les vraies trouvailles y disparaissent.

Le filtre est appliqué à deux endroits :

* côté serveur, via `excludeKeyword` — le bruit ne consomme même pas de
  bande passante ni de place dans la page de 120 résultats ;
* côté local, sur le titre normalisé — filet de sécurité si l'API ignore
  ou tronque `excludeKeyword`.

Chaque terme est choisi pour ne PAS créer de faux négatif sur du vêtement
technique. Les pièges écartés sont commentés : ce sont eux qui coûtent des
trouvailles, et ils ne se voient pas dans une liste plate.
"""

from __future__ import annotations

# ── Cosmétiques, parfums, soins ───────────────────────────────────────────
# Écartés volontairement, malgré leur pertinence apparente :
#   リップ      → sous-chaîne de « リップストップ » (ripstop), tissu des
#                 coupe-vent Nike/ACG. L'exclure supprimerait des trouvailles.
#   クリーム    → « クリーム色 » est un coloris de vêtement.
#   パウダー    → « パウダーブルー » idem.
#   パック      → « 2枚パック » (lot de 2), extrêmement courant.
#   グロス      → collision avec « グログラン » et l'anglais « gross ».
#   サンプル    → trop générique, employé pour tout article de démonstration.
BEAUTY_TERMS: tuple[str, ...] = (
    # Parfumerie
    "香水", "パフューム", "フレグランス",
    "オードトワレ", "オードパルファム", "オーデコロン", "練り香水",
    "アトマイザー",
    "perfume", "parfum", "cologne", "fragrance",
    "eau de toilette", "eau de parfum", "edt", "edp",
    # Maquillage
    "コスメ", "化粧品", "口紅", "ファンデーション", "コンシーラー",
    "アイシャドウ", "アイライナー", "マスカラ", "チーク",
    "ネイル", "マニキュア",
    "lipstick", "mascara", "eyeshadow", "eyeliner", "concealer",
    "foundation", "cosmetic", "cosmetics", "makeup",
    # Soins
    "化粧水", "乳液", "美容液", "美容", "日焼け止め",
    "シャンプー", "コンディショナー", "トリートメント", "ヘアオイル",
    "skincare", "serum", "moisturizer", "shampoo",
)

# ── Articles qui ne sont pas l'objet lui-même ─────────────────────────────
# Un sniper cherche la pièce, pas son emballage ni sa photo.
JUNK_TERMS: tuple[str, ...] = (
    "空箱", "箱のみ", "袋のみ", "タグのみ", "カタログ", "パンフレット",
    "説明書", "チラシ", "ポスター", "ステッカー", "シール",
    "レプリカ", "コピー品", "偽物",
    "ジャンク", "訳あり", "難あり",
    "empty box", "box only", "catalog", "catalogue", "replica",
)

# ── Groupes proposés dans l'interface ─────────────────────────────────────
# Un dict plutôt qu'une liste plate : le dashboard peut ainsi proposer des
# cases à cocher lisibles au lieu d'un pavé de 60 termes.
GROUPS: dict[str, tuple[str, ...]] = {
    "beauty": BEAUTY_TERMS,
    "junk": JUNK_TERMS,
}

GROUP_LABELS: dict[str, str] = {
    "beauty": "Parfums, cosmétiques et soins",
    "junk": "Emballages, copies et articles abîmés",
}

# Actifs par défaut : ce sont exactement les deux familles de bruit qui
# noient un fil quand on suit une marque.
DEFAULT_GROUPS: tuple[str, ...] = ("beauty", "junk")


def terms_for(groups: list[str] | tuple[str, ...]) -> list[str]:
    """Termes correspondant aux groupes demandés, sans doublon, ordre stable."""
    seen: set[str] = set()
    out: list[str] = []
    for name in groups:
        for term in GROUPS.get(name, ()):
            if term not in seen:
                seen.add(term)
                out.append(term)
    return out


def exclude_keyword(terms: list[str], limit: int = 256) -> str:
    """Chaîne `excludeKeyword` pour l'API Mercari.

    L'API attend des mots séparés par des espaces. La longueur acceptée
    n'est pas documentée et n'a pas pu être mesurée (l'API est inaccessible
    depuis l'environnement de développement) : on tronque donc à une taille
    prudente, en coupant sur un terme entier. Le filtre local reprend de
    toute façon ce qui passerait au travers.

    Les termes contenant une espace sont écartés : ils seraient interprétés
    comme plusieurs exclusions indépendantes, ce qui exclurait bien trop.
    """
    parts: list[str] = []
    length = 0
    for term in terms:
        if " " in term:
            continue
        added = len(term) + (1 if parts else 0)
        if length + added > limit:
            break
        parts.append(term)
        length += added
    return " ".join(parts)
