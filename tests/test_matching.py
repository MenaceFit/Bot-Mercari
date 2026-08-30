"""Tests du matching — le cœur métier repris de la v1."""

import pytest

from mercari_sniper.matching import (
    PREMIUM,
    RARE,
    ULTRA,
    Matcher,
    Rule,
    contains_term,
    normalize,
    rarity_of,
)


class TestNormalize:
    def test_lowercases_and_strips_punctuation(self):
        assert normalize("NIKE  ACG!! Vest") == "nike acg vest"

    def test_nfkc_unifies_halfwidth_katakana(self):
        # Cas réel Mercari : certains vendeurs saisissent en demi-chasse.
        assert normalize("ﾅｲｷ") == normalize("ナイキ")

    def test_nfkc_unifies_fullwidth_latin(self):
        assert normalize("ＮＩＫＥ") == "nike"

    def test_collapses_whitespace(self):
        assert normalize("  nike   trail  ") == "nike trail"

    def test_empty_input(self):
        assert normalize("") == ""


class TestRule:
    def test_all_terms_must_be_present(self):
        rule = Rule.compile("ナイキ トレイル")
        assert rule.matches(normalize("ナイキ トレイル ジャケット"), 5000)
        assert not rule.matches(normalize("ナイキ ジャケット"), 5000)

    def test_order_does_not_matter(self):
        rule = Rule.compile("nike trail")
        assert rule.matches(normalize("Trail running by Nike"), 5000)

    def test_exclude_terms_veto(self):
        rule = Rule.compile("nike acg", exclude="ジャンク")
        assert rule.matches(normalize("Nike ACG Vest"), 5000)
        assert not rule.matches(normalize("Nike ACG Vest ジャンク"), 5000)

    def test_price_bounds(self):
        rule = Rule.compile("nike", min_price=3000, max_price=10000)
        assert rule.matches(normalize("nike vest"), 5000)
        assert not rule.matches(normalize("nike vest"), 1000)
        assert not rule.matches(normalize("nike vest"), 50000)

    def test_disabled_rule_never_matches(self):
        rule = Rule.compile("nike", enabled=False)
        assert not rule.matches(normalize("nike vest"), 5000)

    def test_empty_keyword_never_matches(self):
        assert not Rule.compile("").matches(normalize("nike"), 5000)


class TestMatcher:
    KEYWORDS = [
        "ナイキ トレイル",
        "ナイキ ベスト",
        "nike acg",
        "nike trail",
        "under armour storm",
        "windrunner",
    ]

    def test_returns_every_matching_keyword(self):
        matcher = Matcher([Rule.compile(k) for k in self.KEYWORDS])
        hits = matcher.match("ナイキ トレイル ベスト 新品", 5000)
        assert set(hits) == {"ナイキ トレイル", "ナイキ ベスト"}

    def test_no_match_returns_empty(self):
        matcher = Matcher([Rule.compile(k) for k in self.KEYWORDS])
        assert matcher.match("Adidas Ultraboost", 5000) == []

    def test_no_duplicates(self):
        matcher = Matcher([Rule.compile("nike"), Rule.compile("nike")])
        assert matcher.match("nike vest", 5000) == ["nike"]

    def test_index_agrees_with_brute_force(self):
        """L'index inversé ne doit jamais changer le résultat.

        C'est l'optimisation la plus risquée du projet : on la vérifie
        contre l'évaluation naïve de toutes les règles.
        """
        rules = [Rule.compile(k) for k in self.KEYWORDS]
        matcher = Matcher(rules)
        titles = [
            "ナイキ トレイル ウィンドランナー ジャケット",
            "NIKE ACG ベスト メンズ",
            "Under Armour Storm Jacket windrunner",
            "ナイキ ベスト",
            "無関係な商品",
            "windrunner nike trail acg",
        ]
        for title in titles:
            expected = {
                rule.keyword for rule in rules
                if rule.matches(normalize(title), 5000)
            }
            assert set(matcher.match(title, 5000)) == expected, title

    def test_reindex_after_mutation(self):
        matcher = Matcher([Rule.compile("nike")])
        assert matcher.match("adidas gazelle", 5000) == []
        matcher.rules.append(Rule.compile("adidas"))
        matcher.reindex()
        assert matcher.match("adidas gazelle", 5000) == ["adidas"]

    def test_empty_matcher(self):
        assert Matcher([]).match("nike", 5000) == []


class TestRarity:
    def test_ultra_beats_rare(self):
        # "trail" est RARE, "tokyo" est ULTRA : ULTRA doit gagner.
        assert rarity_of("Nike Trail Tokyo Jacket") == ULTRA

    def test_rare_tier(self):
        assert rarity_of("Nike Trail Jacket") == RARE

    def test_default_premium(self):
        assert rarity_of("Nike Basic T-Shirt") == PREMIUM

    def test_japanese_terms_recognised(self):
        assert rarity_of("ナイキ ギャクソウ ジャケット") == ULTRA
        assert rarity_of("ナイキ トレイル パンツ") == RARE

    def test_keyword_contributes_to_rarity(self):
        # Le titre seul est banal, mais le keyword qui a matché est ULTRA.
        assert rarity_of("Veste running", "nike tokyo") == ULTRA


class TestWordBoundaries:
    """Un terme latin s'ancre sur un début de mot, jamais au milieu.

    Sans cet ancrage, une règle courte ramenait n'importe quoi : « air »
    matchait « repair » et « hair dryer ». Avec un ancrage des DEUX côtés,
    elle aurait au contraire raté « airmax », « nikelab », « acgジャケット » —
    les formes composées, qui sont la norme sur Mercari.
    """

    @pytest.mark.parametrize("title", [
        "nike air max 90", "airmax 95", "AIR JORDAN", "veste air",
    ])
    def test_prefix_forms_match(self, title):
        assert Rule.compile("air").matches(normalize(title), 1000)

    @pytest.mark.parametrize("title", [
        "repair kit", "hair dryer", "chair", "fauteuil",
    ])
    def test_inner_occurrences_do_not_match(self, title):
        assert not Rule.compile("air").matches(normalize(title), 1000)

    def test_brand_matches_its_sub_labels(self):
        rule = Rule.compile("nike")
        for title in ("nikelab acg", "nike's jacket", "NIKE"):
            assert rule.matches(normalize(title), 1000), title

    def test_brand_does_not_match_a_word_that_contains_it(self):
        assert not Rule.compile("nike").matches(normalize("unlike new"), 1000)

    def test_japanese_stays_substring_based(self):
        """Le japonais s'écrit sans espace : l'ancrage n'y a aucun sens."""
        rule = Rule.compile("ナイキ")
        assert rule.matches(normalize("ナイキエアマックス90"), 1000)
        assert rule.matches(normalize("ﾅｲｷ トレイル"), 1000)
        assert not rule.matches(normalize("アディダス"), 1000)

    def test_digits_are_part_of_a_word(self):
        assert Rule.compile("90").matches(normalize("air max 90"), 1000)
        assert not Rule.compile("90").matches(normalize("air max 1990"), 1000)

    def test_multi_word_latin_term(self):
        rule = Rule.compile("project rock")
        assert rule.matches(normalize("under armour project rock 5"), 1000)

    def test_exclusion_uses_the_same_boundaries(self):
        rule = Rule.compile("nike", exclude="kids")
        assert not rule.matches(normalize("nike kids sneakers"), 1000)
        # « kids » est bien un préfixe de « kidston » : l'ancrage est en
        # début de mot seulement, donc l'exclusion s'applique. C'est le prix
        # assumé pour que « nike » trouve « nikelab ».
        assert not rule.matches(normalize("nike cath kidston"), 1000)

    def test_matcher_index_honours_boundaries(self):
        matcher = Matcher([Rule.compile("air"), Rule.compile("acg")])
        assert matcher.match("Repair manual for chair", 1000) == []
        assert matcher.match("Nike Air ACG", 1000) == ["air", "acg"]
