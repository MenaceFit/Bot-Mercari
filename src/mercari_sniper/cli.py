"""Point d'entrée en ligne de commande.

Principe directeur : **le programme ne doit jamais disparaître en silence.**
Toute exception est capturée, journalisée, affichée en clair, et la fenêtre
reste ouverte quand on a été lancé par double-clic. Les imports lourds sont
tardifs, pour qu'une dépendance manquante donne un message compréhensible
au lieu d'une trace qui défile avant la fermeture de la console.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import platform
import signal
import sys
import textwrap
import traceback
from pathlib import Path

BANNER = r"""
   __  __                        _   ___       _
  |  \/  |___ _ _ __ __ _ _ _ _ (_) / __|_ _ (_)_ __  ___ _ _
  | |\/| / -_) '_/ _/ _` | '_| || | \__ \ ' \| | '_ \/ -_) '_|
  |_|  |_\___|_| \__\__,_|_|  \_,_| |___/_||_|_| .__/\___|_|
                                               |_|      v2.2
"""

REQUIRED_PACKAGES = [
    ("httpx", "httpx[http2]", "client HTTP asynchrone"),
    ("h2", "httpx[http2]", "support HTTP/2"),
    ("cryptography", "cryptography", "signature DPoP"),
    ("fastapi", "fastapi", "serveur du dashboard"),
    ("uvicorn", "uvicorn[standard]", "serveur ASGI"),
    ("yaml", "PyYAML", "lecture de la configuration"),
]


# ── Diagnostic des dépendances ────────────────────────────────────────────────
def check_dependencies() -> list[tuple[str, str, str]]:
    """Renvoie la liste des paquets manquants, sans rien importer de lourd."""
    import importlib.util

    missing = []
    for module, package, purpose in REQUIRED_PACKAGES:
        if importlib.util.find_spec(module) is None:
            missing.append((module, package, purpose))
    return missing


def report_missing(missing: list[tuple[str, str, str]]) -> None:
    packages = sorted({package for _, package, _ in missing})
    print("\n" + "=" * 64)
    print("  DÉPENDANCES MANQUANTES — le bot ne peut pas démarrer")
    print("=" * 64)
    for module, package, purpose in missing:
        print(f"  ✗ {module:<16} ({purpose})")
    print("\n  Installe-les avec :\n")
    print(f"      {Path(sys.executable).name} -m pip install {' '.join(packages)}")
    print("\n  Ou, plus simplement, toutes d'un coup :\n")
    print(f"      {Path(sys.executable).name} -m pip install -r requirements.txt")
    print("=" * 64 + "\n")


def cmd_doctor(args: argparse.Namespace) -> int:
    """Vérifie l'environnement et explique quoi corriger."""
    print(BANNER)
    print(f"  Python      : {sys.version.split()[0]}  ({sys.executable})")
    print(f"  Plateforme  : {platform.system()} {platform.release()}")
    print(f"  Répertoire  : {Path.cwd()}")

    ok = True

    if sys.version_info < (3, 10):
        print(f"\n  ✗ Python 3.10+ requis (tu as {sys.version.split()[0]})")
        ok = False
    else:
        print("  ✓ Version de Python compatible")

    missing = check_dependencies()
    if missing:
        report_missing(missing)
        ok = False
    else:
        print("  ✓ Toutes les dépendances sont installées")

    config_path = Path(args.config)
    if config_path.exists():
        print(f"  ✓ Configuration trouvée : {config_path}")
        try:
            from .config import Config

            config = Config.load(config_path)
            print(f"      {len(config.keywords)} keywords, {len(config.sources)} sources")
            if not config.keywords:
                print("      → aucun keyword : ajoute-les depuis le dashboard")
        except Exception as exc:
            print(f"  ✗ Configuration illisible : {exc}")
            ok = False
    else:
        print(f"  ⚠ Pas de {config_path} — lance `mercari-sniper init`")

    if os.getenv("DISCORD_WEBHOOK_URL"):
        print("  ✓ Webhook Discord configuré (via l'environnement)")
    else:
        print("  ⚠ DISCORD_WEBHOOK_URL absent — pas de notification Discord")

    # Vérifie qu'on peut écrire là où on va écrire.
    for label, target in (("données", Path("data")), ("journaux", Path("logs"))):
        try:
            target.mkdir(parents=True, exist_ok=True)
            probe = target / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            print(f"  ✓ Dossier {label} accessible en écriture ({target})")
        except OSError as exc:
            print(f"  ✗ Impossible d'écrire dans {target}: {exc}")
            ok = False

    print("\n  " + ("Tout est prêt. Lance `mercari-sniper run`." if ok
                    else "Corrige les points ✗ ci-dessus, puis relance `doctor`."))
    print()
    return 0 if ok else 1


# ── init ──────────────────────────────────────────────────────────────────────
def cmd_init(args: argparse.Namespace) -> int:
    """Crée config.yaml, en reprenant les fichiers du bot v1 s'ils existent."""
    from .config import Config, import_legacy

    config_path = Path(args.config)
    if config_path.exists() and not args.force:
        print(f"  {config_path} existe déjà. Utilise --force pour l'écraser.")
        return 1

    keywords, settings, webhook = import_legacy(args.legacy_dir)

    config = Config()
    config.keywords = keywords
    if settings:
        config.filters.min_price = settings.get("min_price")
        config.filters.max_price = settings.get("max_price")
        config.poll.warmup = settings.get("warmup", True)
    config.ensure_sources()
    config.save(config_path)

    print(f"\n  ✓ Configuration écrite : {config_path}")
    print(f"    {len(config.keywords)} keywords → {len(config.sources)} sources")

    if webhook:
        env_path = Path(".env")
        existing = env_path.read_text("utf-8") if env_path.exists() else ""
        if "DISCORD_WEBHOOK_URL" not in existing:
            with env_path.open("a", encoding="utf-8") as handle:
                handle.write(f"\nDISCORD_WEBHOOK_URL={webhook}\n")
            print(f"  ✓ Webhook Discord déplacé vers {env_path} (git-ignoré)")
        print(
            "\n  ⚠ Ton ancien mercari_config.json contient toujours ce webhook "
            "en clair.\n    Supprime-le ou révoque le webhook si le fichier a "
            "été partagé."
        )

    print("\n  Étape suivante :  mercari-sniper run\n")
    return 0


# ── run ───────────────────────────────────────────────────────────────────────
async def _run_async(args: argparse.Namespace) -> int:
    from .backends import MercariAPIBackend, SimulatorBackend
    from .config import Config, import_legacy_seen
    from .engine import SniperEngine
    from .events import EventBus
    from .logging_setup import setup_logging
    from .notifiers import ConsoleNotifier, DiscordNotifier
    from .store import Store

    config = Config.load(args.config)

    if args.demo:
        config.backend = "simulator"
    if args.port:
        config.server.port = args.port
    if args.host:
        config.server.host = args.host
    elif getattr(args, "lan", False):
        config.server.host = "0.0.0.0"
    if args.interval:
        config.poll.interval = args.interval
    if args.no_dashboard:
        config.server.enabled = False
    if args.verbose:
        config.log_level = "DEBUG"

    log_path = setup_logging(config.log_level)
    import logging

    log = logging.getLogger("sniper")

    print(BANNER)
    # Démarrer sans keyword est normal : on les ajoute depuis le dashboard,
    # et le moteur crée les sources correspondantes à la volée.
    if not config.keywords:
        log.info(
            "Aucun keyword pour l'instant — ajoute-les depuis le dashboard, "
            "ils seront sauvegardés dans %s",
            args.config,
        )

    # ── Assemblage ────────────────────────────────────────────────────────
    if config.backend == "simulator":
        backend = SimulatorBackend(new_items_per_minute=args.demo_rate)
        log.warning("MODE DÉMO : annonces simulées, aucune requête vers Mercari")
    else:
        backend = MercariAPIBackend(proxy=os.getenv("HTTPS_PROXY") or None)

    store = Store(config.storage.database, retention_days=config.storage.retention_days)
    await store.open()

    # Reprise du cache v1 : évite de re-notifier ce que l'ancien bot avait vu.
    legacy_ids = import_legacy_seen(args.legacy_dir)
    if legacy_ids:
        await store.mark_seen_bulk(legacy_ids)
        log.info("cache v1 importé: %d ids", len(legacy_ids))

    bus = EventBus()

    notifiers: list = []
    if config.notify.console:
        notifiers.append(ConsoleNotifier())
    if config.notify.discord_enabled and config.discord_webhook:
        notifiers.append(
            DiscordNotifier(
                config.discord_webhook,
                rate_limit=config.notify.discord_rate_limit,
                max_queue=config.notify.discord_max_queue,
                min_rarity=config.notify.min_rarity,
            )
        )
        log.info("notifications Discord activées")
    else:
        log.warning(
            "Discord inactif — définis DISCORD_WEBHOOK_URL dans .env pour l'activer"
        )

    engine = SniperEngine(config, backend, store, bus, notifiers)

    # ── Arrêt propre ──────────────────────────────────────────────────────
    stop_event = asyncio.Event()

    def request_stop(*_: object) -> None:
        if not stop_event.is_set():
            log.info("arrêt demandé, fermeture en cours…")
            stop_event.set()

    loop = asyncio.get_running_loop()
    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, request_stop)
        except (NotImplementedError, RuntimeError):
            # Windows ne supporte pas add_signal_handler : on retombe sur signal().
            try:
                signal.signal(sig, request_stop)
            except (OSError, ValueError):
                pass

    dashboard = None
    try:
        await engine.start()

        if config.server.enabled:
            from .server import DashboardServer, create_app

            dashboard = DashboardServer(
                create_app(engine), config.server.host, config.server.port
            )
            await dashboard.start()
            # `0.0.0.0` est une adresse d'ÉCOUTE, jamais une destination :
            # un navigateur la refuse (ERR_ADDRESS_INVALID). On n'affiche et
            # on n'ouvre donc que des adresses réellement joignables.
            url, lan_url = dashboard_urls(
                config.server.host, config.server.port, _lan_address()
            )
            log.info("dashboard: %s", url)
            print(f"\n  ➜  Dashboard : {url}")

            if lan_url:
                print(f"  ➜  Depuis ton téléphone : {lan_url}")
            elif is_loopback(config.server.host):
                launcher = "run.bat" if os.name == "nt" else "./run.sh"
                print("  ➜  Téléphone : indisponible — le bot n'écoute que sur")
                print(f"      cet ordinateur. Relance avec  {launcher} --lan")
                print("      pour l'ouvrir au réseau local (même Wi-Fi).")
            print("  ➜  Ctrl+C pour arrêter\n")

            if config.server.open_browser and not args.no_browser:
                asyncio.get_running_loop().call_later(
                    1.0, lambda: _open_browser(url)
                )
        else:
            print("\n  ➜  Dashboard désactivé — Ctrl+C pour arrêter\n")

        if log_path:
            log.info("journal: %s", log_path)

        await stop_event.wait()

    finally:
        if dashboard is not None:
            await dashboard.stop()
        await engine.stop()
        await store.close()
        await backend.aclose()
        log.info("arrêt terminé — %d trouvailles au total", engine.total_hits)

    return 0


# Adresses d'écoute « toutes interfaces » : valides pour bind(), inutilisables
# dans une barre d'adresse. Les confondre donne un ERR_ADDRESS_INVALID.
WILDCARD_HOSTS = frozenset({"0.0.0.0", "::", "[::]", "*", ""})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def is_wildcard(host: str) -> bool:
    return host.strip() in WILDCARD_HOSTS


def is_loopback(host: str) -> bool:
    return host.strip() in LOOPBACK_HOSTS


def dashboard_urls(host: str, port: int, lan: str = "") -> tuple[str, str]:
    """Renvoie (url à ouvrir ici, url à saisir depuis le téléphone).

    La seconde est vide quand le téléphone ne peut pas joindre le bot —
    écoute sur la boucle locale, ou adresse réseau indéterminable.
    """
    host = host.strip()
    local_host = "127.0.0.1" if is_wildcard(host) else host
    local = f"http://{local_host}:{port}"

    if is_loopback(host):
        return local, ""            # personne d'autre ne peut se connecter
    if is_wildcard(host):
        return local, f"http://{lan}:{port}" if lan else ""
    # Écoute sur une interface précise : c'est déjà l'adresse à saisir.
    return local, local


def _lan_address() -> str:
    """Adresse de cette machine sur le réseau local, si on peut la déterminer.

    On ouvre un socket UDP vers une adresse externe : aucun paquet n'est
    envoyé, mais le noyau choisit l'interface de sortie, ce qui révèle
    l'adresse locale utile — plus fiable que résoudre le nom d'hôte.
    """
    import socket

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))   # réseau de documentation, non routé
        return probe.getsockname()[0]
    except OSError:
        return ""
    finally:
        probe.close()


def _open_browser(url: str) -> None:
    try:
        import webbrowser

        webbrowser.open(url)
    except Exception:
        pass  # pas de navigateur : sans conséquence


def cmd_run(args: argparse.Namespace) -> int:
    missing = check_dependencies()
    if missing:
        report_missing(missing)
        return 1
    try:
        return asyncio.run(_run_async(args))
    except KeyboardInterrupt:
        print("\n  Arrêté.")
        return 0


# ── once ──────────────────────────────────────────────────────────────────────
async def _once_async(args: argparse.Namespace) -> int:
    from .backends import MercariAPIBackend, SearchQuery, SimulatorBackend
    from .config import Config
    from .logging_setup import setup_logging
    from .matching import Matcher, Rule, rarity_of

    setup_logging("INFO")
    config = Config.load(args.config)

    keyword = args.keyword or (config.keywords[0] if config.keywords else None)
    if not keyword:
        print("  Fournis un keyword : mercari-sniper once 'ナイキ トレイル'")
        return 1

    backend = (
        SimulatorBackend() if args.demo
        else MercariAPIBackend(proxy=os.getenv("HTTPS_PROXY") or None)
    )
    matcher = Matcher([Rule.compile(kw) for kw in (config.keywords or [keyword])])

    try:
        listings = await backend.search(SearchQuery(keyword=keyword, page_size=args.limit))
        print(f"\n  {len(listings)} annonces pour « {keyword} »\n")
        for listing in listings[: args.limit]:
            matched = matcher.match(listing.title, listing.price)
            rarity, _ = rarity_of(listing.title, matched[0] if matched else "")
            flag = "★" if matched else " "
            print(f"  {flag} [{rarity:<10}] ¥{listing.price:>8,}  {listing.title[:58]}")
            print(f"      {listing.url}")
        print()
        return 0
    except Exception as exc:
        print(f"\n  ✗ Échec : {exc}\n")
        return 1
    finally:
        await backend.aclose()


def cmd_once(args: argparse.Namespace) -> int:
    missing = check_dependencies()
    if missing:
        report_missing(missing)
        return 1
    return asyncio.run(_once_async(args))


# ── Parsing ───────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mercari-sniper",
        description="Détection temps réel des nouvelles annonces Mercari.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            exemples:
              mercari-sniper doctor            vérifie l'installation
              mercari-sniper init              crée config.yaml (importe la v1)
              mercari-sniper run               lance le bot + le dashboard
              mercari-sniper run --demo        démo hors ligne, sans réseau
              mercari-sniper once 'nike acg'   une recherche ponctuelle
            """
        ),
    )
    parser.add_argument("-c", "--config", default="config.yaml", help="chemin du config.yaml")
    parser.add_argument(
        "--legacy-dir", default=".", help="dossier contenant les fichiers du bot v1"
    )

    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="lance le bot et le dashboard")
    p_run.add_argument("--port", type=int, help="port du dashboard")
    p_run.add_argument("--host", help="interface d'écoute")
    p_run.add_argument(
        "--lan",
        action="store_true",
        help="ouvre le dashboard au réseau local (téléphone, même Wi-Fi)",
    )
    p_run.add_argument("--interval", type=float, help="intervalle de poll (s)")
    p_run.add_argument("--demo", action="store_true", help="backend simulé, sans réseau")
    p_run.add_argument(
        "--demo-rate", type=float, default=40.0, help="annonces/minute en démo"
    )
    p_run.add_argument("--no-dashboard", action="store_true", help="console seulement")
    p_run.add_argument("--no-browser", action="store_true", help="n'ouvre pas le navigateur")
    p_run.add_argument("-v", "--verbose", action="store_true", help="logs de débogage")
    p_run.set_defaults(func=cmd_run)

    p_once = sub.add_parser("once", help="une seule recherche, puis quitte")
    p_once.add_argument("keyword", nargs="?", help="terme à rechercher")
    p_once.add_argument("--limit", type=int, default=20, help="nombre d'annonces")
    p_once.add_argument("--demo", action="store_true", help="backend simulé")
    p_once.set_defaults(func=cmd_once)

    p_init = sub.add_parser("init", help="crée config.yaml depuis le bot v1")
    p_init.add_argument("--force", action="store_true", help="écrase la config existante")
    p_init.set_defaults(func=cmd_init)

    p_doctor = sub.add_parser("doctor", help="diagnostique l'installation")
    p_doctor.set_defaults(func=cmd_doctor)

    return parser


def _launched_by_double_click() -> bool:
    """Heuristique : console Windows ouverte juste pour nous.

    Dans ce cas, une sortie immédiate ferme la fenêtre et l'utilisateur ne
    voit jamais l'erreur — c'est précisément le symptôme de la v1.
    """
    if platform.system() != "Windows":
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        pid_buffer = (ctypes.c_uint * 1)()
        count = kernel32.GetConsoleProcessList(pid_buffer, 1)
        return count <= 1
    except Exception:
        return False


SUBCOMMANDS = ("run", "once", "init", "doctor")


_GLOBAL_OPTIONS = ("-c", "--config", "--legacy-dir")


def _default_to_run(argv: list[str]) -> list[str]:
    """Insère `run` quand aucune sous-commande n'est donnée.

    Il faut le faire *avant* argparse : sinon `mercari-sniper --demo` sort en
    SystemExit(2) sur « unrecognized arguments » sans jamais atteindre le repli.
    Les options globales doivent rester devant la sous-commande, d'où
    l'insertion à la bonne position plutôt qu'en tête.
    """
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in ("-h", "--help") or token in SUBCOMMANDS:
            return argv
        if token in _GLOBAL_OPTIONS:
            index += 2          # l'option et sa valeur
            continue
        if any(token.startswith(f"{option}=") for option in _GLOBAL_OPTIONS):
            index += 1
            continue
        return [*argv[:index], "run", *argv[index:]]
    return [*argv, "run"]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(_default_to_run(argv))

    hold = _launched_by_double_click()
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n  Arrêté.")
        return 0
    except Exception:
        print("\n" + "=" * 64)
        print("  ERREUR FATALE — le bot s'est arrêté")
        print("=" * 64)
        traceback.print_exc()
        print("=" * 64)
        print("  Lance `mercari-sniper doctor` pour diagnostiquer.")
        print("  Le détail complet est aussi dans logs/sniper.log")
        print("=" * 64 + "\n")
        return 1
    finally:
        if hold:
            try:
                input("  Appuie sur Entrée pour fermer cette fenêtre… ")
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
