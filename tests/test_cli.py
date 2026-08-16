"""CLI : dispatch des arguments et robustesse du point d'entrée."""

import pytest

from mercari_sniper.cli import (
    _default_to_run,
    build_parser,
    check_dependencies,
    main,
)


class TestDefaultToRun:
    def test_bare_invocation_runs(self):
        assert _default_to_run([]) == ["run"]

    def test_leading_option_gets_run_prepended(self):
        assert _default_to_run(["--demo"]) == ["run", "--demo"]

    def test_explicit_subcommand_untouched(self):
        assert _default_to_run(["run", "--demo"]) == ["run", "--demo"]
        assert _default_to_run(["doctor"]) == ["doctor"]
        assert _default_to_run(["once", "nike"]) == ["once", "nike"]

    def test_global_option_stays_before_subcommand(self):
        """`-c` appartient au parser principal : il doit rester devant `run`."""
        assert _default_to_run(["-c", "x.yaml", "--demo"]) == [
            "-c", "x.yaml", "run", "--demo",
        ]

    def test_global_option_then_explicit_subcommand(self):
        assert _default_to_run(["-c", "x.yaml", "doctor"]) == [
            "-c", "x.yaml", "doctor",
        ]

    def test_only_global_options_appends_run(self):
        assert _default_to_run(["-c", "x.yaml"]) == ["-c", "x.yaml", "run"]

    def test_equals_form_supported(self):
        assert _default_to_run(["--config=x.yaml", "--demo"]) == [
            "--config=x.yaml", "run", "--demo",
        ]

    def test_help_is_never_rewritten(self):
        assert _default_to_run(["--help"]) == ["--help"]
        assert _default_to_run(["-h"]) == ["-h"]


class TestParser:
    def test_every_subcommand_parses(self):
        parser = build_parser()
        for argv in (["run"], ["once", "nike"], ["init"], ["doctor"]):
            assert parser.parse_args(argv).func is not None

    def test_run_options(self):
        args = build_parser().parse_args(
            ["run", "--port", "9000", "--demo", "--no-dashboard", "-v"]
        )
        assert args.port == 9000
        assert args.demo is True
        assert args.no_dashboard is True
        assert args.verbose is True

    def test_config_option_is_global(self):
        args = build_parser().parse_args(["-c", "autre.yaml", "doctor"])
        assert args.config == "autre.yaml"

    def test_once_without_keyword_is_allowed(self):
        assert build_parser().parse_args(["once"]).keyword is None

    def test_unknown_option_exits(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["run", "--option-inexistante"])


class TestDependencyCheck:
    def test_all_dependencies_present_in_test_env(self):
        # Les tests ne tournent que si le paquet est installé : rien ne manque.
        assert check_dependencies() == []


class TestEntryPoint:
    def test_doctor_returns_an_int(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert isinstance(main(["doctor"]), int)

    def test_fatal_error_is_caught_not_propagated(self, monkeypatch):
        """Une exception ne doit jamais remonter : elle fermerait la console."""
        parser_args = build_parser().parse_args(["doctor"])

        def explode(_args):
            raise RuntimeError("panne simulée")

        monkeypatch.setattr(type(parser_args), "func", None, raising=False)
        monkeypatch.setattr("mercari_sniper.cli.cmd_doctor", explode)
        # main() doit renvoyer 1, pas propager l'exception.
        assert main(["doctor"]) == 1

    def test_keyboard_interrupt_exits_cleanly(self, monkeypatch):
        def interrupt(_args):
            raise KeyboardInterrupt

        monkeypatch.setattr("mercari_sniper.cli.cmd_doctor", interrupt)
        assert main(["doctor"]) == 0
