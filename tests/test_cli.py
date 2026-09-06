"""CLI : dispatch des arguments et robustesse du point d'entrée."""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from mercari_sniper.cli import (
    _default_to_run,
    build_parser,
    check_dependencies,
    dashboard_urls,
    is_loopback,
    is_wildcard,
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


class TestLauncher:
    """Régression : le projet est en layout `src/`.

    `python -m mercari_sniper` échoue tant que le paquet n'est pas installé.
    `main.py` doit donc rester le point d'entrée des lanceurs, et les
    lanceurs doivent installer quelque chose.
    """

    def test_main_py_exists_at_root(self):
        assert (REPO_ROOT / "main.py").is_file()

    def test_main_py_adds_src_to_path(self):
        source = (REPO_ROOT / "main.py").read_text("utf-8")
        assert "sys.path.insert" in source
        assert '"src"' in source

    def test_main_py_runs_as_subprocess(self, tmp_path):
        """Le lanceur doit démarrer sans ModuleNotFoundError.

        Ne jamais chercher « Mercari » dans la sortie : le rapport affiche le
        répertoire courant, et pendant longtemps l'assertion n'a été vérifiée
        que parce que le dépôt s'appelle « Bot-Mercari ». Extrait ailleurs, le
        même code faisait échouer le test sans qu'il n'y ait rien de cassé.
        On s'appuie donc sur des lignes que le programme écrit lui-même.
        """
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "main.py"), "doctor"],
            capture_output=True,
            text=True,
            cwd=tmp_path,          # hors du dépôt : le chemin doit être résolu seul
            timeout=90,
        )
        output = result.stdout + result.stderr
        assert "No module named mercari_sniper" not in output
        assert "Python" in result.stdout
        assert "Plateforme" in result.stdout
        assert "dépendances" in result.stdout

    #: Les lanceurs de la v2. « run.bat » et « run.sh » lancent désormais la
    #: génération courante — voir TestDefaultLauncher plus bas.
    LEGACY = ["run-legacy-v2.sh", "run-legacy-v2.bat", "diagnostic.bat"]

    @pytest.mark.parametrize("launcher", LEGACY)
    def test_launchers_use_main_py(self, launcher):
        source = (REPO_ROOT / launcher).read_text("utf-8", errors="replace")
        assert "main.py" in source, f"{launcher} doit lancer via main.py"
        assert "-m mercari_sniper" not in source, (
            f"{launcher} utilise `-m mercari_sniper`, qui échoue sans installation"
        )

    @pytest.mark.parametrize(
        "launcher",
        ["run.sh", "run.bat", "run-legacy-v2.sh", "run-legacy-v2.bat"],
    )
    def test_launchers_install_dependencies(self, launcher):
        source = (REPO_ROOT / launcher).read_text("utf-8", errors="replace")
        assert "pip install" in source
        # Le repli sur requirements.txt doit exister si `-e .` échoue.
        assert "requirements.txt" in source

    @pytest.mark.parametrize(
        "launcher",
        ["run.sh", "run.bat", "run-legacy-v2.sh", "run-legacy-v2.bat"],
    )
    def test_launchers_verify_pip_not_just_the_interpreter(self, launcher):
        """Un venv peut exister SANS pip — vérifier python.exe ne suffit pas.

        C'est ce qui bloquait au démarrage : l'environnement était créé mais
        inutilisable, et le script le considérait comme prêt.
        """
        source = (REPO_ROOT / launcher).read_text("utf-8", errors="replace")
        assert "-m pip --version" in source, "pip doit être testé, pas supposé"

    @pytest.mark.parametrize(
        "launcher",
        ["run.sh", "run.bat", "run-legacy-v2.sh", "run-legacy-v2.bat"],
    )
    def test_launchers_repair_a_broken_venv(self, launcher):
        source = (REPO_ROOT / launcher).read_text("utf-8", errors="replace")
        # Trois niveaux : réparer, reconstruire, puis se replier sur le
        # Python du système — le bot n'a pas besoin d'un venv pour tourner.
        assert "ensurepip" in source
        assert "--user" in source


class TestDefaultLauncher:
    """Le fichier qu'on double-clique doit lancer la version courante.

    Régression vécue : « run.bat » lançait encore Mercari Sniper v2. Le
    dashboard s'ouvrait, s'intitulait « Mercari Sniper », n'interrogeait
    qu'une marketplace et n'avait pas Telegram — sans que rien n'indique
    que ce n'était pas le bon programme.
    """

    @pytest.mark.parametrize("launcher", ["run.sh", "run.bat"])
    def test_default_launcher_starts_the_radar(self, launcher):
        source = (REPO_ROOT / launcher).read_text("utf-8", errors="replace")
        assert "buyee_radar run" in source, (
            f"{launcher} doit lancer la génération courante"
        )
        assert "main.py run" not in source

    @pytest.mark.parametrize("launcher", ["run.sh", "run.bat"])
    def test_default_launcher_calibrates_first(self, launcher):
        """Sans sélecteurs, les sources réelles ne ramènent rien."""
        source = (REPO_ROOT / launcher).read_text("utf-8", errors="replace")
        assert "calibrate --if-needed" in source

    @pytest.mark.parametrize("launcher", ["run-legacy-v2.sh", "run-legacy-v2.bat"])
    def test_legacy_launcher_says_it_is_the_old_one(self, launcher):
        source = (REPO_ROOT / launcher).read_text("utf-8", errors="replace")
        assert "ANCIENNE version" in source
        assert "run.bat" in source or "run.sh" in source


class TestDashboardUrls:
    """Régression : `0.0.0.0` était affiché et ouvert comme une URL.

    C'est une adresse d'écoute, pas une destination — Chrome répond
    ERR_ADDRESS_INVALID. Le dashboard était bien démarré, mais l'onglet
    ouvert automatiquement affichait une erreur.
    """

    def test_wildcard_is_never_browsable(self):
        local, _ = dashboard_urls("0.0.0.0", 8420, "192.168.1.20")
        assert local == "http://127.0.0.1:8420"
        assert "0.0.0.0" not in local

    def test_ipv6_wildcard_is_never_browsable(self):
        for host in ("::", "[::]", ""):
            local, _ = dashboard_urls(host, 8420, "192.168.1.20")
            assert local == "http://127.0.0.1:8420"

    def test_wildcard_exposes_the_lan_address_for_the_phone(self):
        _, lan = dashboard_urls("0.0.0.0", 8420, "192.168.1.20")
        assert lan == "http://192.168.1.20:8420"

    def test_wildcard_without_known_lan_address_offers_nothing(self):
        _, lan = dashboard_urls("0.0.0.0", 8420, "")
        assert lan == ""

    def test_loopback_admits_the_phone_cannot_connect(self):
        """Même si l'adresse LAN est connue : rien n'écoute dessus."""
        local, lan = dashboard_urls("127.0.0.1", 8420, "192.168.1.20")
        assert local == "http://127.0.0.1:8420"
        assert lan == ""

    def test_localhost_is_treated_as_loopback(self):
        assert dashboard_urls("localhost", 8420, "192.168.1.20")[1] == ""

    def test_explicit_interface_is_its_own_phone_address(self):
        local, lan = dashboard_urls("192.168.1.20", 8420, "192.168.1.20")
        assert local == lan == "http://192.168.1.20:8420"

    def test_port_is_honoured(self):
        assert dashboard_urls("0.0.0.0", 9000, "10.0.0.5") == (
            "http://127.0.0.1:9000",
            "http://10.0.0.5:9000",
        )

    def test_host_classification(self):
        assert is_wildcard("0.0.0.0") and is_wildcard("::")
        assert not is_wildcard("192.168.1.20")
        assert is_loopback("127.0.0.1") and is_loopback("localhost")
        assert not is_loopback("0.0.0.0")


class TestLanFlag:
    """`--lan` évite d'avoir à éditer config.yaml pour utiliser le téléphone."""

    def test_flag_parses(self):
        assert build_parser().parse_args(["run", "--lan"]).lan is True

    def test_absent_by_default(self):
        assert build_parser().parse_args(["run"]).lan is False

    def test_explicit_host_wins_over_lan(self):
        args = build_parser().parse_args(["run", "--lan", "--host", "127.0.0.1"])
        assert args.host == "127.0.0.1" and args.lan is True

    @pytest.mark.parametrize("launcher", ["run.sh", "run.bat"])
    def test_launchers_forward_arguments(self, launcher):
        """`run.bat --lan` doit atteindre le CLI."""
        source = (REPO_ROOT / launcher).read_text("utf-8", errors="replace")
        assert "%*" in source or '"$@"' in source


class TestStartupBanner:
    """Bout en bout : ce qui s'affiche au démarrage doit être ouvrable.

    Le bug d'origine ne venait pas du serveur — il écoutait bien — mais du
    texte imprimé et de l'URL passée au navigateur.
    """

    def _launch(self, tmp_path, *extra):
        import socket

        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()

        proc = subprocess.Popen(
            [
                sys.executable, str(REPO_ROOT / "main.py"), "run",
                "--demo", "--no-browser", "--port", str(port), *extra,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=tmp_path,
        )
        try:
            out = ""
            for _ in range(200):
                line = proc.stdout.readline()
                if not line:
                    break
                out += line
                if "Ctrl+C" in line:
                    break
            return out
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()

    def test_lan_mode_never_prints_the_bind_address(self, tmp_path):
        out = self._launch(tmp_path, "--lan")
        assert "Dashboard :" in out, out
        assert "0.0.0.0" not in out, out
        assert "http://127.0.0.1:" in out, out

    def test_loopback_mode_says_the_phone_cannot_connect(self, tmp_path):
        out = self._launch(tmp_path)
        assert "http://127.0.0.1:" in out, out
        assert "--lan" in out, out


class TestMobileLauncher:
    """Un raccourci double-cliquable pour le mode téléphone.

    Éditer `config.yaml` ou taper une option en ligne de commande n'a rien
    d'évident sous Windows.
    """

    def test_wrapper_exists(self):
        assert (REPO_ROOT / "run-mobile.bat").is_file()

    def test_wrapper_delegates_with_lan(self):
        source = (REPO_ROOT / "run-mobile.bat").read_text("ascii")
        assert "run.bat" in source
        assert "--lan" in source
        assert "%*" in source            # les autres options passent aussi

    def test_wrapper_is_ascii_for_the_windows_console(self):
        """La console Windows n'est pas en UTF-8 : pas d'accents dans un .bat."""
        (REPO_ROOT / "run-mobile.bat").read_text("ascii")

    def test_wrapper_uses_crlf(self):
        assert b"\r\n" in (REPO_ROOT / "run-mobile.bat").read_bytes()
