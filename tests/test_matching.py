"""Tests du matching — le cœur métier repris de la v1."""

from mercari_sniper.matching import (
    PREMIUM,
    RARE,
    ULTRA,
    Matcher,
    Rule,
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
