"""Rendement et exhaustivité : ce qui décide combien d'annonces on trouve.

Trois plaintes de terrain, trois causes distinctes, testées ici :

* « il trouve quasiment rien »  → requêtes trop larges, budget gaspillé
* « il loupe des annonces »     → trou entre deux scans non détecté
* « il remonte des parfums »    → bruit de marque non filtré
"""

import time

import pytest

from mercari_sniper.backends.base import SearchPage, SearchQuery
from mercari_sniper.config import Config, SourceConfig
from mercari_sniper.engine import SniperEngine
from mercari_sniper.events import EventBus
from mercari_sniper.models import Listing
from mercari_sniper.store import Store


class RecordingBackend:
    """Enregistre les requêtes reçues et rejoue des pages scriptées."""

    name = "recording"

    def __init__(self, pages=None):
        self.pages = list(pages or [])
        self.queries: list[SearchQuery] = []

    async def search(self, query: SearchQuery):
        self.queries.append(query)
        if not self.pages:
            return SearchPage(items=[])
        page = self.pages.pop(0)
        return page if isinstance(page, SearchPage) else SearchPage(items=page)

    async def aclose(self):
        return None


def listing(item_id, title, created, price=5000):
    return Listing(
        id=item_id,
        title=title,
        price=price,
        url=f"https://jp.mercari.com/item/{item_id}",
        created=int(created),
        detected_at=time.time(),
    )


@pytest.fixture
async def ctx(tmp_path):
    config = Config()
    config.storage.database = str(tmp_path / "y.db")
    config.notify.console = False
    config.poll.warmup = True
    store = Store(config.storage.database)
    await store.open()
    yield config, store, EventBus()
    await store.close()


# ── 1. Trous entre deux scans ─────────────────────────────────────────────
class TestGapDetection:
    """Une page pleine et plus récente que le dernier passage = trou.

    L'ancien critère exigeait que la page soit inédite à 100 %. Sur une page
    de 120, 119 nouveautés ne le déclenchaient pas — alors que la page était
    déjà saturée et qu'il en manquait forcément derrière. C'est la maille par
    laquelle les annonces s'échappaient.
    """

    def _engine(self, ctx):
        config, store, bus = ctx
        config.keywords = ["nike acg"]
        config.sources = [SourceConfig(query="nike acg", page_size=4)]
        return SniperEngine(config, RecordingBackend(), store, bus)

    def test_page_newer_than_watermark_is_a_gap(self, ctx):
        engine = self._engine(ctx)
        state = engine._sources["nike acg"]
        state.watermark = 1000

        page = [listing(f"m{i}", "Nike ACG", created=1100 + i) for i in range(4)]
        assert engine._has_gap(state, page) is True

    def test_page_reaching_back_to_watermark_is_not_a_gap(self, ctx):
        engine = self._engine(ctx)
        state = engine._sources["nike acg"]
        state.watermark = 1000

        # La plus ancienne de la page est antérieure au repère : la
        # continuité est établie, rien n'a pu passer entre les deux.
        page = [listing(f"m{i}", "Nike ACG", created=990 + i * 10) for i in range(4)]
        assert engine._has_gap(state, page) is False

    def test_almost_all_new_still_detected(self, ctx):
        """Le cas que l'ancien code laissait passer : 3 nouveautés sur 4."""
        engine = self._engine(ctx)
        state = engine._sources["nike acg"]
        state.watermark = 1000
        engine._seen.add("m0")

        page = [listing(f"m{i}", "Nike ACG", created=1100 + i) for i in range(4)]
        assert engine._has_gap(state, page) is True

    def test_no_watermark_falls_back_to_paginating(self, ctx):
        """Sans repère, on pagine par précaution.

        Se tromper ici coûte une requête ; se tromper dans l'autre sens
        coûte une annonce ratée. Le biais penche donc vers la pagination.
        """
        engine = self._engine(ctx)
        state = engine._sources["nike acg"]
        page = [listing("m1", "Nike ACG", created=1100)]
        assert engine._has_gap(state, page) is True

        engine._seen.add("m1")
        assert engine._has_gap(state, page) is False

    def test_undated_page_falls_back_to_all_new(self, ctx):
        engine = self._engine(ctx)
        state = engine._sources["nike acg"]
        state.watermark = 1000

        page = [listing(f"m{i}", "Nike ACG", created=0) for i in range(3)]
        assert engine._has_gap(state, page) is True
        engine._seen.add("m1")
        assert engine._has_gap(state, page) is False

    def test_watermark_rises_with_each_page(self, ctx):
        engine = self._engine(ctx)
        state = engine._sources["nike acg"]
        engine._raise_watermark(state, [listing("m1", "x", created=500)])
        assert state.watermark == 500
        engine._raise_watermark(state, [listing("m2", "x", created=400)])
        assert state.watermark == 500, "le repère ne doit jamais reculer"
        engine._raise_watermark(state, [listing("m3", "x", created=900)])
        assert state.watermark == 900


# ── 2. Cadence : une source calme reste rapide ────────────────────────────
class TestCadence:
    """Ralentir une source silencieuse est un contresens pour un sniper.

    Une requête précise ne rapporte rien pendant des heures, puis l'annonce
    tombe. La version précédente l'espaçait jusqu'à 60 s au motif qu'elle
    « ne rapportait rien » — soit une détection jusqu'à une minute trop tard,
    quand la pièce est déjà vendue.
    """

    def _engine(self, ctx):
        config, store, bus = ctx
        config.keywords = ["nike acg"]
        config.sources = [SourceConfig(query="nike acg")]
        config.poll.interval = 2.0
        return SniperEngine(config, RecordingBackend(), store, bus)

    def test_quiet_source_keeps_its_interval(self, ctx):
        engine = self._engine(ctx)
        state = engine._sources["nike acg"]
        start = state.interval

        state.polls = 50
        state.fill_ratio = 0.0          # totalement silencieuse
        for _ in range(20):
            engine._adapt_interval(state)

        assert state.interval == start, "une source calme ne doit pas ralentir"

    def test_saturated_source_speeds_up(self, ctx):
        engine = self._engine(ctx)
        state = engine._sources["nike acg"]
        state.fill_ratio = 0.9
        engine._adapt_interval(state)
        assert state.interval < 2.0

    def test_speed_up_stops_at_min_interval(self, ctx):
        engine = self._engine(ctx)
        state = engine._sources["nike acg"]
        state.fill_ratio = 0.9
        for _ in range(50):
            engine._adapt_interval(state)
        assert state.interval >= engine.config.poll.min_interval


# ── 3. Répartition du budget ──────────────────────────────────────────────
class TestBudget:
    """Quand la demande dépasse le débit autorisé, il faut le dire.

    Sans répartition explicite, les sources visent toutes leur intervalle
    idéal et s'empilent sur le token bucket : le retard s'accumule en
    silence et des annonces passent entre deux scans.
    """

    def _engine(self, ctx, n, rate):
        config, store, bus = ctx
        config.keywords = [f"kw{i}" for i in range(n)]
        config.sources = [SourceConfig(query=f"kw{i}") for i in range(n)]
        config.poll.interval = 1.0
        config.poll.min_interval = 1.0
        config.poll.global_rate_limit = rate
        return SniperEngine(config, RecordingBackend(), store, bus)

    def test_intervals_are_stretched_when_over_budget(self, ctx):
        engine = self._engine(ctx, n=20, rate=4.0)
        report = engine._allocate_budget()

        assert report["throttled_sources"] == 20
        # 20 sources pour 4 req/s : au mieux une visite toutes les 5 s.
        for state in engine._sources.values():
            assert state.interval == pytest.approx(5.0, rel=0.01)

    def test_intervals_untouched_when_budget_suffices(self, ctx):
        engine = self._engine(ctx, n=2, rate=8.0)
        report = engine._allocate_budget()
        assert report["throttled_sources"] == 0
        for state in engine._sources.values():
            assert state.interval == 1.0

    def test_weight_buys_a_faster_cadence(self, ctx):
        config, store, bus = ctx
        config.keywords = ["a", "b"]
        config.sources = [
            SourceConfig(query="a", weight=3.0),
            SourceConfig(query="b", weight=1.0),
        ]
        config.poll.global_rate_limit = 0.4     # volontairement trop juste
        engine = SniperEngine(config, RecordingBackend(), store, bus)
        engine._allocate_budget()

        assert engine._sources["a"].interval < engine._sources["b"].interval

    def test_coverage_reports_the_real_cadence(self, ctx):
        engine = self._engine(ctx, n=20, rate=4.0)
        engine._allocate_budget()
        coverage = engine.coverage()

        assert coverage["saturated"] is True
        assert coverage["effective_interval"] == pytest.approx(5.0, rel=0.01)

    def test_manual_interval_is_never_overridden(self, ctx):
        config, store, bus = ctx
        config.keywords = ["a"]
        config.sources = [SourceConfig(query="a", interval=30.0)]
        config.poll.global_rate_limit = 0.01
        engine = SniperEngine(config, RecordingBackend(), store, bus)
        engine._allocate_budget()
        assert engine._sources["a"].interval == 30.0


# ── 4. Remplacement des requêtes trop larges ──────────────────────────────
class TestAutoSplit:
    """Une requête large qui ne rapporte rien est remplacée par des précises.

    C'est la réparation automatique des config.yaml produits par l'ancienne
    version, qui écrivait « ナイキ » là où il fallait « ナイキ トレイル ».
    """

    def _engine(self, ctx):
        config, store, bus = ctx
        config.keywords = ["nike acg", "nike trail"]
        config.sources = [SourceConfig(query="nike", auto=True)]
        config.poll.split_min_items = 100
        config.poll.split_min_yield = 0.01
        return SniperEngine(config, RecordingBackend(), store, bus)

    def test_low_yield_source_is_flagged(self, ctx):
        engine = self._engine(ctx)
        state = engine._sources["nike"]
        state.items_seen = 500
        state.hits = 1                       # 0,2 % de rendement

        assert [s.config.query for s in engine._split_candidates()] == ["nike"]

    def test_evidence_is_required_before_flagging(self, ctx):
        """Sur 10 annonces vues, le rendement ne veut rien dire."""
        engine = self._engine(ctx)
        state = engine._sources["nike"]
        state.items_seen = 10
        state.hits = 0
        assert engine._split_candidates() == []

    def test_productive_broad_source_is_left_alone(self, ctx):
        engine = self._engine(ctx)
        state = engine._sources["nike"]
        state.items_seen = 500
        state.hits = 50                      # 10 % : elle mérite son budget
        assert engine._split_candidates() == []

    def test_manual_source_is_never_replaced(self, ctx):
        engine = self._engine(ctx)
        state = engine._sources["nike"]
        state.config.auto = False
        state.items_seen = 500
        state.hits = 0
        assert engine._split_candidates() == []

    async def test_split_creates_one_source_per_keyword(self, ctx):
        engine = self._engine(ctx)
        state = engine._sources["nike"]
        state.items_seen = 500
        state.hits = 0

        created = await engine._split_source(state)

        assert sorted(created) == ["nike acg", "nike trail"]
        assert "nike" not in engine._sources
        assert engine._sources["nike acg"].split_from == "nike"
        assert [s.query for s in engine.config.sources] == ["nike acg", "nike trail"]

    async def test_split_is_skipped_without_anything_more_precise(self, ctx):
        config, store, bus = ctx
        config.keywords = ["nike"]
        config.sources = [SourceConfig(query="nike", auto=True)]
        engine = SniperEngine(config, RecordingBackend(), store, bus)
        state = engine._sources["nike"]
        state.items_seen = 500
        state.hits = 0

        assert await engine._split_source(state) == []
        assert "nike" in engine._sources, "on ne laisse pas le keyword sans source"

    def test_yield_ratio_is_zero_without_data(self, ctx):
        engine = self._engine(ctx)
        assert engine._sources["nike"].yield_ratio == 0.0


# ── 5. Filtrage du bruit ──────────────────────────────────────────────────
class TestNoiseFiltering:
    """« il peut trouver des parfums Dior ou du maquillage de luxe »."""

    def _engine(self, ctx, keyword="dior"):
        config, store, bus = ctx
        config.keywords = [keyword]
        config.sources = [SourceConfig(query=keyword)]
        return SniperEngine(config, RecordingBackend(), store, bus)

    @pytest.mark.parametrize("title", [
        "ディオール 香水 30ml",
        "DIOR オードパルファム",
        "dior lipstick rouge",
        "ディオール コスメ セット",
        "Dior perfume miss dior",
    ])
    def test_beauty_is_rejected(self, ctx, title):
        engine = self._engine(ctx)
        assert engine._is_excluded(title) is True

    @pytest.mark.parametrize("title", [
        "ディオール ジャケット",
        "dior sneakers 27cm",
        "ナイキ リップストップ ジャケット",   # ripstop, PAS du rouge à lèvres
        "nike windrunner cream colour",     # « cream » comme coloris
        "adidas 2枚パック tシャツ",           # « pack », pas un masque
    ])
    def test_clothing_is_kept(self, ctx, title):
        engine = self._engine(ctx)
        assert engine._is_excluded(title) is False

    def test_rejections_are_counted(self, ctx):
        engine = self._engine(ctx)
        item = listing("m1", "dior 香水 30ml", created=time.time())
        assert engine._emit(item) is False
        assert engine.drops["bruit"] == 1

    def test_noise_reaches_the_server_side_filter(self, ctx):
        engine = self._engine(ctx)
        query = engine._query_for(engine._sources["dior"])
        assert "香水" in query.exclude_keyword
        assert query.exclude_keyword, "le bruit doit être écarté par Mercari"

    def test_user_exclusions_are_added(self, ctx):
        config, store, bus = ctx
        config.keywords = ["nike"]
        config.sources = [SourceConfig(query="nike")]
        config.filters.exclude_words = ["キッズ"]
        engine = SniperEngine(config, RecordingBackend(), store, bus)
        assert engine._is_excluded("ナイキ キッズ スニーカー") is True

    def test_filter_can_be_turned_off(self, ctx):
        config, store, bus = ctx
        config.keywords = ["dior"]
        config.sources = [SourceConfig(query="dior")]
        config.filters.noise_groups = []
        engine = SniperEngine(config, RecordingBackend(), store, bus)
        assert engine._is_excluded("ディオール 香水") is False

    def test_source_exclusion_is_kept_alongside_noise(self, ctx):
        config, store, bus = ctx
        config.keywords = ["nike"]
        config.sources = [SourceConfig(query="nike", exclude_keyword="キッズ")]
        engine = SniperEngine(config, RecordingBackend(), store, bus)
        exclude = engine._query_for(engine._sources["nike"]).exclude_keyword
        assert "キッズ" in exclude and "香水" in exclude


# ── 6. Preuve de régression, exécutable sur l'ancienne version ────────────
class TestAgainstThePreviousBehaviour:
    """Tests écrits pour tourner AUSSI sur le code d'avant.

    Ils n'utilisent que des points d'entrée qui existaient déjà, afin de
    documenter ce qui a changé par une exécution, pas par une affirmation.
    """

    async def test_gap_is_seen_even_when_the_page_is_not_all_new(self, ctx):
        """Le cas exact que l'ancien critère laissait passer.

        Le cache de déduplication est GLOBAL, partagé par toutes les sources.
        Il suffit donc qu'une autre source ait déjà croisé une annonce pour
        que la page de celle-ci ne soit plus « entièrement inédite » — et
        l'ancien code en concluait qu'il n'y avait pas de trou, alors que sa
        page ne remontait pas du tout jusqu'à son passage précédent.

        Ici : la source a vu jusqu'à t=1000, sa page ne descend qu'à t=1500.
        Tout ce qui a été publié entre les deux est perdu si on ne pagine pas.
        """
        config, store, bus = ctx
        config.keywords = ["nike trail"]
        config.sources = [SourceConfig(query="nike trail", page_size=4)]
        config.poll.warmup = False

        first = [listing("m0", "Nike Trail", created=1000)]
        page2 = SearchPage(
            items=[
                listing("n0", "Nike Trail 0", created=2003),
                listing("n1", "Nike Trail 1", created=2002),
                listing("n2", "Nike Trail 2", created=2001),
                # Déjà croisée par une AUTRE source : la page n'est donc pas
                # « entièrement inédite », mais le trou existe bel et bien.
                listing("shared", "Nike Trail X", created=1500),
            ],
            next_page_token="p2",
        )
        recovered = SearchPage(items=[listing("m0", "Nike Trail", created=1000)])

        backend = RecordingBackend([first, page2, recovered])
        engine = SniperEngine(config, backend, store, bus)
        state = engine._sources["nike trail"]

        await engine._poll_source(state)          # pose le repère à t=1000
        engine._seen.add("shared")                # une autre source l'a vue
        await engine._poll_source(state)

        assert len(backend.queries) == 3, (
            "trou non rattrapé : la page ne remontait pas au scan précédent, "
            "mais une annonce déjà connue a suffi à masquer le problème"
        )
        assert backend.queries[-1].page_token == "p2"

    def test_brand_keyword_does_not_surface_perfume(self, ctx):
        """Avant : « dior » remontait les parfums et le maquillage."""
        config, store, bus = ctx
        config.keywords = ["dior"]
        config.sources = [SourceConfig(query="dior")]
        engine = SniperEngine(config, RecordingBackend(), store, bus)

        perfume = listing("m1", "dior 香水 オードパルファム 50ml", created=time.time())
        assert engine._emit(perfume) is False, "un parfum ne doit jamais remonter"

    async def test_added_keyword_is_searched_verbatim(self, ctx):
        """Avant : « nike acg » interrogeait « nike », 99 % hors sujet."""
        config, store, bus = ctx
        config.keywords = []
        config.sources = []
        engine = SniperEngine(config, RecordingBackend(), store, bus)

        result = await engine.add_keyword("nike acg")
        assert result["source_created"] == "nike acg"
