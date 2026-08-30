"""Vocabulaire de bruit : ce qu'il attrape, et surtout ce qu'il ne doit pas.

Un filtre trop large est pire que pas de filtre : il fait rater des pièces
sans laisser de trace. Les faux positifs testés ici sont ceux qui coûtent
réellement des trouvailles sur du vêtement technique.
"""

import pytest

from mercari_sniper import noise
from mercari_sniper.matching import contains_term, normalize


def caught(title: str, groups=noise.DEFAULT_GROUPS) -> bool:
    normalized = normalize(title)
    return any(
        contains_term(normalized, normalize(term))
        for term in noise.terms_for(groups)
    )


class TestBeautyIsCaught:
    @pytest.mark.parametrize("title", [
        "DIOR 香水 ミス ディオール 50ml",
        "シャネル オードトワレ",
        "ディオール オードパルファム 未使用",
        "ルイヴィトン コスメ ポーチ 化粧品",
        "YSL 口紅 リップスティック",
        "Dior perfume 100ml new",
        "Chanel eau de parfum",
        "MAC lipstick set",
        "資生堂 美容液 30ml",
        "ロクシタン シャンプー トリートメント",
        "エルメス 練り香水",
        "gucci fragrance edp",
    ])
    def test_beauty_terms(self, title):
        assert caught(title), title


class TestJunkIsCaught:
    @pytest.mark.parametrize("title", [
        "ナイキ 空箱 のみ",
        "シュプリーム ステッカー 5枚",
        "アークテリクス カタログ 2020",
        "nike box only no shoes",
        "ノースフェイス レプリカ",
    ])
    def test_junk_terms(self, title):
        assert caught(title), title


class TestClothingSurvives:
    """Les pièges qui feraient rater de vraies trouvailles.

    Chacun de ces titres contient une sous-chaîne d'un terme de bruit
    plausible. Ce sont eux qui ont dicté la composition de la liste.
    """

    @pytest.mark.parametrize("title", [
        # リップ est une sous-chaîne de リップストップ (ripstop), le tissu
        # des coupe-vent Nike ACG. Exclure リップ coûterait la catégorie.
        "ナイキ ACG リップストップ ジャケット",
        "nike windrunner ripstop nylon",
        # クリーム et パウダー sont des coloris.
        "ナイキ ヴィンテージ クリーム色 パーカー",
        "adidas powder blue track top",
        # パック = lot, très courant.
        "ユニクロ tシャツ 2枚パック",
        # « foundation » n'est ici que dans un nom de modèle latin composé.
        "arcteryx veilance jacket size m",
        "under armour project rock hoodie",
        "ナイキ トレイル ランニング ベスト",
        "ギャクソウ gyakusou ハーフパンツ",
        "nike acg smith summit cargo pants",
    ])
    def test_not_caught(self, title):
        assert not caught(title), title


class TestGroups:
    def test_default_groups_are_the_two_families(self):
        assert set(noise.DEFAULT_GROUPS) == {"beauty", "junk"}

    def test_every_group_has_a_label(self):
        assert set(noise.GROUPS) == set(noise.GROUP_LABELS)

    def test_terms_for_deduplicates_and_keeps_order(self):
        terms = noise.terms_for(["beauty", "beauty"])
        assert terms == noise.terms_for(["beauty"])
        assert len(terms) == len(set(terms))

    def test_unknown_group_is_ignored(self):
        assert noise.terms_for(["inexistant"]) == []

    def test_empty_selection_disables_the_filter(self):
        assert noise.terms_for([]) == []
        assert not caught("DIOR 香水", groups=[])


class TestExcludeKeyword:
    """La chaîne envoyée à Mercari pour qu'il filtre lui-même."""

    def test_terms_are_space_separated(self):
        built = noise.exclude_keyword(["香水", "コスメ"])
        assert built == "香水 コスメ"

    def test_multi_word_terms_are_dropped(self):
        """« eau de toilette » deviendrait trois exclusions indépendantes."""
        built = noise.exclude_keyword(["香水", "eau de toilette", "コスメ"])
        assert "eau" not in built
        assert built == "香水 コスメ"

    def test_length_is_capped_on_a_whole_term(self):
        built = noise.exclude_keyword(["aaaa", "bbbb", "cccc"], limit=9)
        assert built == "aaaa bbbb"
        assert len(built) <= 9

    def test_empty_input(self):
        assert noise.exclude_keyword([]) == ""

    def test_default_vocabulary_produces_something_usable(self):
        built = noise.exclude_keyword(noise.terms_for(noise.DEFAULT_GROUPS))
        assert "香水" in built
        assert len(built) <= 256


class TestPriorityInTheServerSideString:
    """La troncature ne doit jamais sacrifier ce que l'utilisateur a saisi."""

    def test_user_words_come_first(self):
        from mercari_sniper.config import FiltersConfig

        filters = FiltersConfig(exclude_words=["キッズ", "ジュニア"])
        terms = filters.all_exclude_terms()
        assert terms[:2] == ["キッズ", "ジュニア"]

    def test_user_words_survive_the_length_cap(self):
        from mercari_sniper.config import FiltersConfig

        filters = FiltersConfig(exclude_words=["キッズ"])
        built = noise.exclude_keyword(filters.all_exclude_terms())
        assert built.startswith("キッズ")

    def test_no_duplicate_when_user_repeats_a_builtin_term(self):
        from mercari_sniper.config import FiltersConfig

        filters = FiltersConfig(exclude_words=["香水"])
        terms = filters.all_exclude_terms()
        assert terms.count("香水") == 1
