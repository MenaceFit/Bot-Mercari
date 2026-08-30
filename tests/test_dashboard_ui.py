"""Garde-fous sur la coquille web.

Ces tests lisent les fichiers statiques. Ils n'ouvrent pas de navigateur —
ils verrouillent les règles qui, ici, ont déjà cassé quelque chose une fois,
et que rien d'autre ne rattrape.
"""

from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "src" / "mercari_sniper" / "web"
CSS = (WEB / "app.css").read_text("utf-8")
JS = (WEB / "app.js").read_text("utf-8")
HTML = (WEB / "index.html").read_text("utf-8")


class TestGridTracksCanShrink:
    """`1fr` vaut `minmax(auto, 1fr)` : la piste ne descend pas sous la
    largeur min-content de son contenu, et la page déborde latéralement sans
    qu'aucun élément ne paraisse fautif. Le piège s'est déjà refermé une fois
    sur ce projet, dans le tutoriel d'installation.
    """

    def test_single_column_layout_uses_minmax_zero(self):
        assert ".columns { grid-template-columns: minmax(0, 1fr); }" in CSS
        assert ".columns { grid-template-columns: 1fr; }" not in CSS

    def test_flex_columns_can_shrink(self):
        assert ".side, .feed-col { min-width: 0; }" in CSS

    def test_kpi_tracks_are_bounded(self):
        """Chaque piste de la grille de mesures doit pouvoir rétrécir."""
        assert "repeat(5, minmax(0, 1fr))" in CSS


class TestColorJobsStaySeparate:
    """Les couleurs de statut sont réservées aux états.

    Les réutiliser pour une magnitude (ici la jauge de rendement) brouille
    les deux lectures : l'orange ne veut plus dire « attention », il veut
    aussi dire « peu ».
    """

    def test_yield_meter_uses_a_single_sequential_hue(self):
        assert ".src__yield i { display: block; height: 100%; background: var(--accent); }" in CSS
        assert "--serious" not in CSS.split(".src__yield")[1].split("/*")[0]

    def test_no_dead_state_class_remains(self):
        assert "src__yield--dead" not in CSS
        assert "src__yield--dead" not in JS


class TestChartIsReadable:
    def test_chart_reports_its_scale(self):
        """Sans repère de grandeur, la même courbe vaut pour 1 ou 200/min."""
        assert 'id="chartScale"' in HTML
        assert "chartScale" in JS

    def test_scale_is_cleared_when_empty(self):
        empty = JS.split("Aucune trouvaille sur la période")[1][:200]
        assert "chartScale" in empty

    def test_stroke_does_not_stretch_with_the_tile(self):
        """preserveAspectRatio=\"none\" étire aussi l'épaisseur du trait."""
        assert 'preserveAspectRatio="none"' in HTML
        assert "vector-effect: non-scaling-stroke" in CSS

    def test_chart_spans_the_full_row(self):
        assert ".kpis .tile--chart { grid-column: 1 / -1; }" in CSS


class TestFilterPanel:
    def test_panel_exists(self):
        for anchor in ('id="filterGroups"', 'id="excludeList"', 'id="excludeForm"'):
            assert anchor in HTML, anchor

    def test_panel_is_rendered_and_wired(self):
        assert "function renderFilters()" in JS
        assert "async function saveFilters(" in JS
        assert "'/api/filters'" in JS

    def test_filters_survive_the_periodic_refresh(self):
        """Le rafraîchissement de 5 s ne doit pas écraser les filtres."""
        block = JS.split("async function refreshAggregates()")[1].split("}")[0]
        assert "state.filters" in block

    def test_split_event_is_surfaced(self):
        """Le remplacement d'une requête change la liste sous les yeux de
        l'utilisateur : il doit savoir pourquoi."""
        assert "case 'source_split'" in JS


class TestDiagnostics:
    @pytest.mark.parametrize("field", [
        "effective_interval", "target_interval", "low_yield",
    ])
    def test_coverage_fields_are_used(self, field):
        assert field in JS

    def test_yield_kpi_exists(self):
        assert 'id="kpiYield"' in HTML
        assert "yield_ratio" in JS

    def test_drops_are_shown(self):
        """Un filtre trop strict doit être visible, pas silencieux."""
        assert "s.drops" in JS


class TestOddTileOnMobile:
    def test_last_stat_tile_spans_both_columns(self):
        """Cinq tuiles sur deux colonnes laissaient une case vide."""
        assert 'class="tile tile--odd"' in HTML
        assert ".tile--odd { grid-column: 1 / -1; }" in CSS
