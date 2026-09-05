"""Configuration, secrets, Telegram, Discord, et le point d'entrée CLI."""

import os
from pathlib import Path

import pytest
import yaml

from snipe.config.settings import Settings, load_dotenv
from snipe.main import _default_to_run, build_parser, check_dependencies
from snipe.notifications.discord import DiscordNotifier
from snipe.notifications.telegram import CAPTION_LIMIT, TelegramNotifier

from .helpers import make_listing

EXAMPLE = Path(__file__).resolve().parents[2] / "src" / "snipe" / "config.example.yaml"


class TestSettings:
    def test_example_config_parses(self):
        raw = yaml.safe_load(EXAMPLE.read_text("utf-8"))
        settings = Settings.from_dict(raw)
        assert settings.keywords
        assert "mercari" in settings.sources

    def test_keyword_as_plain_string(self):
        settings = Settings.from_dict({"keywords": ["Nike Division"]})
        assert settings.keywords[0].name == "Nike Division"

    def test_unknown_fields_are_ignored(self):
        """Une clé en trop dans le YAML ne doit pas empêcher le démarrage."""
        settings = Settings.from_dict({
            "scanner": {"budget_per_second": 4.0, "champ_inconnu": 1},
        })
        assert settings.scanner.budget_per_second == 4.0

    def test_intervals_map(self):
        settings = Settings()
        assert set(settings.scanner.intervals) == {"high", "medium", "low"}

    def test_only_enabled_sources_are_kept_by_the_builder(self):
        from snipe.app import build_sources

        settings = Settings.from_dict({
            "sources": {"mercari": {"enabled": False}},
        })
        assert build_sources(settings) == {}


class TestSecretsNeverLeak:
    """Le YAML est fait pour être partagé ; un secret dedans finit publié."""

    def test_secrets_come_only_from_the_environment(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100999")
        monkeypatch.chdir(tmp_path)

        settings = Settings.load(tmp_path / "absent.yaml")
        assert settings.telegram_token == "123:ABC"
        assert settings.telegram_chat_id == "-100999"

    def test_secrets_are_absent_from_the_serialised_config(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:SECRET")
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/x")
        settings = Settings()
        settings.apply_env()

        dumped = yaml.safe_dump(settings.to_yaml_dict(), allow_unicode=True)
        assert "SECRET" not in dumped
        assert "webhooks" not in dumped

    def test_saving_never_writes_a_secret(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:SECRET")
        settings = Settings()
        settings.apply_env()
        path = settings.save(tmp_path / "config.yaml")
        assert "SECRET" not in path.read_text("utf-8")

    def test_the_guard_actually_fires(self):
        """Le garde-fou doit lever, pas filtrer en silence."""
        from snipe.config.settings import _assert_no_secrets

        with pytest.raises(AssertionError, match="telegram_token"):
            _assert_no_secrets({"notifications": {"telegram_token": "x"}})

    def test_example_config_contains_no_secret_field(self):
        raw = yaml.safe_load(EXAMPLE.read_text("utf-8"))
        from snipe.config.settings import _assert_no_secrets

        _assert_no_secrets(raw)     # ne doit pas lever

    def test_dotenv_does_not_override_the_real_environment(self, tmp_path, monkeypatch):
        """Un export en console doit gagner sur le fichier."""
        monkeypatch.chdir(tmp_path)
        Path(".env").write_text("TELEGRAM_CHAT_ID=depuis_le_fichier\n", "utf-8")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "depuis_le_shell")
        load_dotenv(".env")
        assert os.environ["TELEGRAM_CHAT_ID"] == "depuis_le_shell"

    def test_dotenv_is_read_when_the_variable_is_absent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        Path(".env").write_text('TELEGRAM_CHAT_ID="-100123"\n', "utf-8")
        load_dotenv(".env")
        assert os.environ["TELEGRAM_CHAT_ID"] == "-100123"


class TestTelegramFormatting:
    def _notifier(self):
        return TelegramNotifier("123:ABC", "-100", enabled=True)

    def test_message_contains_the_essentials(self):
        listing = make_listing(price=8900, source="mercari")
        listing.keyword = "Nike Division"
        text = self._notifier().format(listing)

        assert "8 900" in text
        assert "mercari" in text
        assert "Nike Division" in text

    def test_html_in_a_title_is_escaped(self):
        """Un titre est du texte écrit par un inconnu : sans échappement,
        un « < » suffit à faire rejeter le message par l'API."""
        listing = make_listing(title="<b>ナイキ</b> & <script>alert(1)</script>")
        text = self._notifier().format(listing)
        assert "<script>" not in text
        assert "&lt;" in text

    def test_latency_shown_only_when_measured(self):
        """Afficher « détectée en 0,0 s » serait une fausse promesse."""
        listing = make_listing(published_at=0.0)
        assert "après publication" not in self._notifier().format(listing)

    def test_latency_shown_when_known(self):
        import time
        now = time.time()
        listing = make_listing(published_at=now - 3.0, received_at=now)
        assert "après publication" in self._notifier().format(listing)

    def test_buy_link_is_included(self):
        listing = make_listing()
        listing.buy_url = "https://buyee.jp/item/mercari/item/m1"
        assert "Commander via Buyee" in self._notifier().format(listing)

    def test_caption_fits_the_api_limit(self):
        listing = make_listing(title="ナイキ " * 400)
        assert len(self._notifier().format(listing)[:CAPTION_LIMIT]) <= CAPTION_LIMIT

    def test_disabled_without_credentials(self):
        assert TelegramNotifier("", "", enabled=True).enabled is False

    def test_enabled_with_credentials(self):
        assert TelegramNotifier("123:ABC", "-100").enabled is True


class TestDiscordFormatting:
    def test_embed_fields(self):
        notifier = DiscordNotifier("https://discord.com/api/webhooks/1/x")
        listing = make_listing(price=8900)
        listing.keyword = "Nike Division"
        embed = notifier.build_embed(listing)

        names = [field["name"] for field in embed["fields"]]
        assert "Prix" in names and "Source" in names and "Mot-clé" in names

    def test_disabled_without_webhook(self):
        assert DiscordNotifier("", enabled=True).enabled is False

    def test_detection_field_only_when_measured(self):
        notifier = DiscordNotifier("https://discord.com/api/webhooks/1/x")
        listing = make_listing(published_at=0.0)
        names = [f["name"] for f in notifier.build_embed(listing)["fields"]]
        assert "Détection" not in names


class TestCLI:
    def test_all_subcommands_parse(self):
        parser = build_parser()
        for argv in (["run"], ["benchmark"], ["doctor"], ["init"]):
            assert parser.parse_args(argv).func is not None

    def test_run_options(self):
        args = build_parser().parse_args(
            ["run", "--demo", "--dry-run", "--once", "--budget", "3"]
        )
        assert args.demo and args.dry_run and args.once and args.budget == 3.0

    def test_bare_invocation_runs(self):
        assert _default_to_run([]) == ["run"]

    def test_leading_option_gets_run_prepended(self):
        assert _default_to_run(["--dry-run"]) == ["run", "--dry-run"]

    def test_global_config_stays_ahead_of_the_subcommand(self):
        assert _default_to_run(["-c", "x.yaml", "--demo"]) == [
            "-c", "x.yaml", "run", "--demo",
        ]

    def test_explicit_subcommand_untouched(self):
        assert _default_to_run(["benchmark"]) == ["benchmark"]

    def test_help_is_never_rewritten(self):
        assert _default_to_run(["--help"]) == ["--help"]

    def test_dependencies_are_present_in_the_test_env(self):
        assert check_dependencies() == []
