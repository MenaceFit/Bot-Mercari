"""Configuration, dérivation des sources et migration depuis la v1."""

import json

import pytest

from mercari_sniper.config import (
    Config,
    import_legacy,
    import_legacy_seen,
    load_dotenv,
)


class TestDefaults:
    def test_sane_defaults(self):
        config = Config()
        assert config.backend == "mercari"
        assert config.poll.interval > 0
        assert config.poll.global_rate_limit > 0
        assert config.server.port == 8420

    def test_missing_file_yields_defaults(self, tmp_path):
        config = Config.load(tmp_path / "absent.yaml")
        assert config.keywords == []


class TestSourceDerivation:
    """Une source par keyword, interrogé tel quel.

    Le regroupement par racine (« nike trail » + « nike acg » → « nike ») a
    été retiré : il économisait des requêtes mais ruinait le rendement.
    Mercari ne renvoie que les 120 annonces les plus récentes ; sur « nike »
    elles couvrent quelques secondes et sont presque toutes hors sujet, donc
    le budget se consumait pour rien ET la page débordait quand même.
    """

    def test_each_keyword_is_queried_verbatim(self):
        config = Config()
        config.keywords = ["nike trail", "nike acg", "nike phenom"]
        config.ensure_sources()

        assert [source.query for source in config.sources] == [
            "nike trail", "nike acg", "nike phenom",
        ]

    def test_no_keyword_is_widened_to_its_first_word(self):
        """Régression : c'est l'élargissement qui faisait tout rater."""
        config = Config()
        config.keywords = ["ナイキ トレイル", "nike acg"]
        config.ensure_sources()

        queries = {source.query for source in config.sources}
        assert "ナイキ" not in queries
        assert "nike" not in queries

    def test_duplicate_keywords_share_one_source(self):
        config = Config()
        config.keywords = ["nike acg", "nike  acg", "nike acg"]
        config.ensure_sources()
        assert len(config.sources) == 1

    def test_derived_sources_are_marked_auto(self):
        """Seules les sources auto peuvent être remplacées ou retirées."""
        config = Config()
        config.keywords = ["nike acg"]
        config.ensure_sources()
        assert all(source.auto for source in config.sources)

    def test_explicit_sources_are_not_overwritten(self):
        config = Config.from_dict({
            "keywords": ["nike trail", "nike acg"],
            "sources": [{"query": "custom"}],
        })
        config.ensure_sources()
        assert [source.query for source in config.sources] == ["custom"]

    def test_no_keywords_yields_no_sources(self):
        config = Config()
        config.ensure_sources()
        assert config.sources == []


class TestSerialization:
    def test_roundtrip(self, tmp_path):
        path = tmp_path / "config.yaml"
        config = Config()
        config.keywords = ["ナイキ トレイル", "nike acg"]
        config.poll.interval = 3.5
        config.filters.min_price = 2000
        config.save(path)

        reloaded = Config.load(path)
        assert reloaded.keywords == ["ナイキ トレイル", "nike acg"]
        assert reloaded.poll.interval == 3.5
        assert reloaded.filters.min_price == 2000

    def test_secrets_never_written_to_yaml(self, tmp_path, monkeypatch):
        """Garde-fou : le webhook ne doit jamais atterrir dans le YAML."""
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/SECRET")
        path = tmp_path / "config.yaml"
        config = Config()
        config.apply_env()
        assert config.discord_webhook.endswith("SECRET")

        config.save(path)
        assert "SECRET" not in path.read_text("utf-8")
        assert "discord_webhook" not in path.read_text("utf-8")

    def test_unknown_keys_are_ignored(self):
        config = Config.from_dict({"poll": {"interval": 5, "champ_inconnu": 1}})
        assert config.poll.interval == 5


class TestEnvOverrides:
    def test_env_overrides_yaml(self, monkeypatch):
        monkeypatch.setenv("SNIPER_PORT", "9999")
        monkeypatch.setenv("SNIPER_INTERVAL", "7.5")
        monkeypatch.setenv("SNIPER_BACKEND", "simulator")
        config = Config()
        config.apply_env()

        assert config.server.port == 9999
        assert config.poll.interval == 7.5
        assert config.backend == "simulator"

    def test_invalid_env_value_is_ignored(self, monkeypatch):
        monkeypatch.setenv("SNIPER_PORT", "pas-un-nombre")
        config = Config()
        config.apply_env()
        assert config.server.port == 8420   # valeur par défaut conservée


class TestDotenv:
    def test_reads_key_values(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text('# commentaire\nFOO=bar\nQUOTED="baz"\n\n', encoding="utf-8")
        monkeypatch.delenv("FOO", raising=False)
        load_dotenv(env)
        import os

        assert os.environ["FOO"] == "bar"
        assert os.environ["QUOTED"] == "baz"

    def test_existing_environment_wins(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("FOO=depuis_fichier\n", encoding="utf-8")
        monkeypatch.setenv("FOO", "depuis_shell")
        load_dotenv(env)
        import os

        assert os.environ["FOO"] == "depuis_shell"

    def test_missing_file_is_noop(self, tmp_path):
        load_dotenv(tmp_path / "absent")   # ne doit pas lever


class TestLegacyImport:
    def test_imports_keywords_and_settings(self, tmp_path):
        (tmp_path / "keywords.json").write_text(
            json.dumps({"keywords": ["ナイキ トレイル", "nike acg"], "version": "6.0"}),
            encoding="utf-8",
        )
        (tmp_path / "mercari_config.json").write_text(
            json.dumps({
                "webhook_url": "https://discord.com/api/webhooks/xyz",
                "min_price": 3000,
                "max_price": 50000,
                "warmup_first_run": False,
            }),
            encoding="utf-8",
        )

        keywords, settings, webhook = import_legacy(tmp_path)
        assert keywords == ["ナイキ トレイル", "nike acg"]
        assert settings["min_price"] == 3000
        assert settings["warmup"] is False
        assert webhook.endswith("xyz")

    def test_missing_files_are_tolerated(self, tmp_path):
        keywords, settings, webhook = import_legacy(tmp_path)
        assert keywords == []
        assert webhook == ""

    def test_corrupt_json_does_not_raise(self, tmp_path):
        (tmp_path / "keywords.json").write_text("{ pas du json", encoding="utf-8")
        keywords, _, _ = import_legacy(tmp_path)
        assert keywords == []

    def test_seen_cache_import_from_dict(self, tmp_path):
        (tmp_path / "seen_items.json").write_text(
            json.dumps({"m1": 1700000000, "m2": 1700000001}), encoding="utf-8"
        )
        assert set(import_legacy_seen(tmp_path)) == {"m1", "m2"}

    def test_seen_cache_import_from_list(self, tmp_path):
        (tmp_path / "seen_items.json").write_text(
            json.dumps(["m1", "m2"]), encoding="utf-8"
        )
        assert set(import_legacy_seen(tmp_path)) == {"m1", "m2"}

    def test_seen_cache_absent(self, tmp_path):
        assert import_legacy_seen(tmp_path) == []


class TestSourceMigration:
    """Réparation des config.yaml écrits par les versions ≤ 2.2.

    Elles dérivaient la requête du premier mot du keyword, ce qui faisait
    partir presque tout le budget de scan dans des annonces hors sujet.
    """

    def test_broad_auto_source_is_replaced(self):
        config = Config.from_dict({
            "keywords": ["ナイキ トレイル", "ナイキ ベスト"],
            "sources": [{"query": "ナイキ", "auto": True}],
        })
        replaced = config.migrate_sources()

        assert replaced == ["ナイキ"]
        assert [s.query for s in config.sources] == ["ナイキ トレイル", "ナイキ ベスト"]
        assert all(s.auto for s in config.sources)

    def test_manual_broad_source_is_kept(self):
        """Une requête large écrite à la main est un choix, pas une erreur."""
        config = Config.from_dict({
            "keywords": ["ナイキ トレイル"],
            "sources": [{"query": "ナイキ", "auto": False}],
        })
        assert config.migrate_sources() == []
        assert [s.query for s in config.sources] == ["ナイキ"]

    def test_precise_source_is_left_alone(self):
        config = Config.from_dict({
            "keywords": ["nike acg"],
            "sources": [{"query": "nike acg", "auto": True}],
        })
        assert config.migrate_sources() == []
        assert [s.query for s in config.sources] == ["nike acg"]

    def test_orphan_auto_source_is_kept(self):
        """Plus aucun keyword ne la couvre : ce n'est pas à la migration
        de la supprimer, le moteur s'en charge à l'exécution."""
        config = Config.from_dict({
            "keywords": ["adidas"],
            "sources": [{"query": "ナイキ", "auto": True}],
        })
        assert config.migrate_sources() == []

    def test_no_duplicate_when_the_precise_source_already_exists(self):
        config = Config.from_dict({
            "keywords": ["nike acg", "nike trail"],
            "sources": [
                {"query": "nike", "auto": True},
                {"query": "nike acg", "auto": True},
            ],
        })
        config.migrate_sources()
        queries = [s.query for s in config.sources]
        # L'ordre est un détail ; ce qui compte est qu'aucune requête ne soit
        # envoyée deux fois — ce serait du budget dépensé pour rien.
        assert sorted(queries) == ["nike acg", "nike trail"]
        assert len(queries) == len(set(queries))

    def test_migration_runs_and_persists_on_load(self, tmp_path):
        import yaml

        path = tmp_path / "config.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "keywords": ["ナイキ トレイル"],
                    "sources": [{"query": "ナイキ", "auto": True}],
                },
                allow_unicode=True,
            ),
            "utf-8",
        )

        config = Config.load(path)
        assert [s.query for s in config.sources] == ["ナイキ トレイル"]

        # Réécrit sur le disque : le correctif ne doit pas être à refaire
        # à chaque démarrage.
        again = yaml.safe_load(path.read_text("utf-8"))
        assert [s["query"] for s in again["sources"]] == ["ナイキ トレイル"]

    def test_a_clean_config_is_not_rewritten(self, tmp_path):
        import yaml

        path = tmp_path / "config.yaml"
        path.write_text(
            yaml.safe_dump({"keywords": ["nike acg"],
                            "sources": [{"query": "nike acg", "auto": True}]}),
            "utf-8",
        )
        before = path.read_text("utf-8")
        Config.load(path)
        assert path.read_text("utf-8") == before
