"""Point d'entrée en ligne de commande.

    python -m snipe run          bot complet
    python -m snipe run --demo   sans réseau, marketplace simulée
    python -m snipe --dry-run    scanne et affiche, n'envoie rien
    python -m snipe --once       un seul cycle, puis sort
    python -m snipe --benchmark  mesure les latences par étape
    python -m snipe doctor       diagnostic de l'installation
    python -m snipe init         crée config.yaml

Deux principes de robustesse, appris de la version précédente :

* **Les imports lourds sont tardifs.** Une dépendance manquante doit produire
  un message clair, pas une trace au lancement suivie d'une fenêtre qui se
  ferme avant qu'on ait pu lire.
* **`main()` n'a pas le droit de laisser passer une exception.** Sur un
  double-clic Windows, une exception non attrapée ferme la console avec la
  trace dedans.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
from pathlib import Path

BANNER = r"""
   ____       _
  / ___| _ __ (_)_ __   ___
  \___ \| '_ \| | '_ \ / _ \    monitoring multi-marketplace
   ___) | | | | | |_) |  __/    orienté faible latence
  |____/|_| |_|_| .__/ \___|
                |_|
"""

REQUIRED = {
    "httpx": "httpx[http2]",
    "yaml": "pyyaml",
    "cryptography": "cryptography",
    "selectolax": "selectolax",
}


def check_dependencies() -> list[str]:
    missing = []
    for module, package in REQUIRED.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    return missing


def report_missing(missing: list[str]) -> None:
    print("\n  Il manque des dépendances :\n")
    for package in missing:
        print(f"    - {package}")
    print(f"\n  Installe-les avec :\n\n    {sys.executable} -m pip install "
          + " ".join(missing) + "\n")


def setup_logging(level: str = "INFO") -> Path | None:
    """Console + fichier. Un dossier non inscriptible ne doit pas tuer le démarrage."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(
        logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)-7s [%(name)s] %(message)s",
                          datefmt="%H:%M:%S")
    )
    root.addHandler(console)

    try:
        Path("logs").mkdir(exist_ok=True)
        file_handler = logging.FileHandler("logs/snipe.log", encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s")
        )
        root.addHandler(file_handler)
        return Path("logs/snipe.log")
    except OSError as exc:
        root.warning("journal fichier indisponible (%s) — console uniquement", exc)
        return None


# ── run ───────────────────────────────────────────────────────────────────
async def _run(args) -> int:
    from .app import build_hub, build_scanner, build_sources
    from .config.settings import Settings
    from .storage.sqlite import Storage

    settings = Settings.load(args.config)
    if args.budget:
        settings.scanner.budget_per_second = args.budget
    if args.verbose:
        settings.log_level = "DEBUG"

    log_path = setup_logging(settings.log_level)
    log = logging.getLogger("snipe")

    if not settings.keywords:
        log.warning(
            "aucun mot-clé dans %s — le bot tournera sans rien chercher. "
            "Lance `python -m snipe init` pour partir d'un exemple.",
            settings.path,
        )

    storage = Storage(settings.storage.database)
    await storage.open()

    # Un seul objet de métriques, partagé par le hub et le scanner : sinon
    # les notifications alimenteraient un compteur que personne n'affiche.
    from .core.metrics import Metrics
    metrics = Metrics()

    hub = build_hub(
        settings,
        dry_run=args.dry_run,
        metrics=metrics,
        on_sent=lambda listing, channel, ok, ms, err: storage.queue_notification(
            listing.key, channel, ok, ms, err
        ),
    )
    await hub.start()

    sources = build_sources(settings, force_simulator=args.demo)
    scanner = build_scanner(settings, sources, storage, hub, metrics=metrics)

    if args.dry_run:
        log.warning("MODE SIMULATION : les annonces sont affichées, rien n'est envoyé")
    if args.demo:
        log.warning("MODE DÉMO : marketplace simulée, aucune requête vers l'extérieur")

    await scanner.start()

    if args.once:
        # Un seul tour : on laisse partir chaque requête une fois, on vide
        # les files, on sort. C'est le mode utilisé en intégration continue.
        await asyncio.sleep(args.once_seconds)
        await scanner.stop()
        await hub.drain()
        print(scanner.metrics.render())
        await hub.stop()
        await storage.close()
        for source in sources.values():
            await source.close()
        return 0

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            try:
                signal.signal(sig, lambda *_: stop.set())
            except (OSError, ValueError):
                pass

    health = await scanner.health_check()
    for name, report in health.items():
        marker = "OK" if report["ok"] else "PANNE"
        suffix = "" if report["verified"] else "  (source non vérifiée en réel)"
        log.info("source %-16s %-6s %s%s", name, marker, report["detail"], suffix)

    print(f"\n  Ctrl+C pour arrêter.  Journal : {log_path or 'console'}\n")

    status_task = asyncio.create_task(
        _status_loop(scanner, settings.scanner.status_every, stop)
    )
    await stop.wait()
    status_task.cancel()

    await scanner.stop()
    await hub.stop()
    await storage.close()
    for source in sources.values():
        await source.close()
    print(scanner.metrics.render())
    return 0


async def _status_loop(scanner, every: float, stop) -> None:
    try:
        while not stop.is_set():
            await asyncio.sleep(every)
            print(scanner.metrics.render())
    except asyncio.CancelledError:
        pass


def cmd_run(args) -> int:
    missing = check_dependencies()
    if missing:
        report_missing(missing)
        return 1
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\n  Arrêté.")
        return 0


# ── benchmark ─────────────────────────────────────────────────────────────
def cmd_benchmark(args) -> int:
    missing = check_dependencies()
    if missing:
        report_missing(missing)
        return 1

    from . import benchmark
    from .app import build_keywords
    from .config.settings import Settings

    setup_logging("WARNING")
    settings = Settings.load(args.config)
    keywords = build_keywords(settings)
    if not keywords:
        from .core.keywords import Keyword
        keywords = [
            Keyword.build("Nike Division", search=["ナイキ"],
                          include=["ディビジョン", "division"]),
            Keyword.build("Nike Trail", search=["ナイキ"], include=["トレイル", "trail"]),
        ]
    report = asyncio.run(benchmark.run(keywords))
    print(benchmark.render(report))
    return 0


# ── doctor ────────────────────────────────────────────────────────────────
def cmd_doctor(args) -> int:
    import platform

    print(BANNER)
    print(f"  Python      : {platform.python_version()}  ({sys.executable})")
    print(f"  Plateforme  : {platform.system()} {platform.release()}")
    print(f"  Répertoire  : {os.getcwd()}")
    print()

    ok = True
    if sys.version_info < (3, 10):
        print("  [X] Python 3.10+ requis")
        ok = False
    else:
        print("  [OK] Version de Python compatible")

    missing = check_dependencies()
    if missing:
        print(f"  [X] Dépendances manquantes : {', '.join(missing)}")
        ok = False
    else:
        print("  [OK] Toutes les dépendances sont installées")

    config_path = Path(args.config or "config.yaml")
    if not config_path.exists():
        print(f"  [!] {config_path} absent — lance `python -m snipe init`")
    else:
        try:
            from .config.settings import Settings
            settings = Settings.load(config_path)
            active = [n for n, s in settings.sources.items() if s.enabled]
            print(f"  [OK] {config_path} lu : {len(settings.keywords)} mot(s)-clé(s), "
                  f"source(s) active(s) : {', '.join(active) or 'aucune'}")

            if settings.telegram_token and settings.telegram_chat_id:
                print("  [OK] Telegram configuré (jeton et chat présents dans .env)")
                if not args.no_network:
                    ok &= _check_telegram(settings)
            elif settings.notifications.telegram_enabled:
                print("  [!] Telegram activé mais TELEGRAM_BOT_TOKEN / "
                      "TELEGRAM_CHAT_ID absents de .env")
            if settings.discord_webhook:
                print("  [OK] Discord configuré")
        except Exception as exc:
            print(f"  [X] {config_path} illisible : {exc}")
            ok = False

    for folder in ("data", "logs"):
        try:
            Path(folder).mkdir(exist_ok=True)
            probe = Path(folder) / ".probe"
            probe.write_text("x")
            probe.unlink()
            print(f"  [OK] Dossier « {folder} » accessible en écriture")
        except OSError as exc:
            print(f"  [X] Dossier « {folder} » non inscriptible : {exc}")
            ok = False

    print()
    print("  Tout est prêt." if ok else "  Corrige les points [X] ci-dessus.")
    print()
    return 0 if ok else 1


def _check_telegram(settings) -> bool:
    from .notifications.telegram import TelegramNotifier

    async def check():
        notifier = TelegramNotifier(settings.telegram_token, settings.telegram_chat_id)
        try:
            return await notifier.verify()
        finally:
            await notifier.close()

    try:
        ok, detail = asyncio.run(check())
    except Exception as exc:
        print(f"  [!] Telegram non joignable : {exc}")
        return True          # réseau indisponible n'est pas une erreur de config
    print(("  [OK] Telegram : " if ok else "  [X] Telegram : ") + detail)
    return ok


# ── init ──────────────────────────────────────────────────────────────────
def cmd_init(args) -> int:
    target = Path(args.config or "config.yaml")
    if target.exists() and not args.force:
        print(f"\n  {target} existe déjà. Utilise --force pour l'écraser.\n")
        return 1

    example = Path(__file__).parent / "config.example.yaml"
    if not example.exists():
        print(f"\n  Modèle introuvable : {example}\n")
        return 1
    target.write_text(example.read_text("utf-8"), encoding="utf-8")
    print(f"\n  {target} créé depuis le modèle.")
    print("  Ajoute tes mots-clés, puis lance :  python -m snipe run --demo\n")
    return 0


# ── argparse ──────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="snipe",
        description="Monitoring multi-marketplace orienté faible latence.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-c", "--config", default="config.yaml",
                        help="chemin du fichier de configuration")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="lance le bot")
    run.add_argument("--demo", action="store_true",
                     help="marketplace simulée, aucune requête réseau")
    run.add_argument("--dry-run", action="store_true",
                     help="scanne et affiche, n'envoie aucune notification")
    run.add_argument("--once", action="store_true",
                     help="un seul cycle de scan, puis sortie")
    run.add_argument("--once-seconds", type=float, default=12.0,
                     help="durée du cycle unique (défaut : 12 s)")
    run.add_argument("--budget", type=float, help="requêtes/seconde, toutes sources")
    run.add_argument("-v", "--verbose", action="store_true", help="logs de débogage")
    run.set_defaults(func=cmd_run)

    bench = sub.add_parser("benchmark", help="mesure les latences par étape")
    bench.set_defaults(func=cmd_benchmark)

    doctor = sub.add_parser("doctor", help="diagnostic de l'installation")
    doctor.add_argument("--no-network", action="store_true",
                        help="ne teste pas la connexion Telegram")
    doctor.set_defaults(func=cmd_doctor)

    init = sub.add_parser("init", help="crée config.yaml depuis le modèle")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    return parser


def _default_to_run(argv: list[str]) -> list[str]:
    """`snipe --dry-run` doit marcher comme `snipe run --dry-run`.

    Les options globales (`-c`) restent devant le sous-commande, sinon
    argparse les refuse.
    """
    commands = {"run", "benchmark", "doctor", "init"}
    if any(arg in commands for arg in argv):
        return argv
    if any(arg in ("-h", "--help") for arg in argv):
        return argv

    globals_ = []
    rest = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("-c", "--config"):
            globals_.extend(argv[index:index + 2])
            index += 2
            continue
        if arg.startswith("--config="):
            globals_.append(arg)
            index += 1
            continue
        rest = argv[index:]
        break
    return globals_ + ["run"] + rest


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    try:
        args = parser.parse_args(_default_to_run(argv))
    except SystemExit as exc:
        return int(exc.code or 0)

    if not getattr(args, "func", None):
        parser.print_help()
        return 0

    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\n  Arrêté.")
        return 0
    except Exception:
        # Rien ne doit remonter : sur un double-clic Windows, une exception
        # non attrapée ferme la console avec la trace dedans.
        import traceback
        print("\n  Erreur inattendue :\n")
        traceback.print_exc()
        print("\n  Détails complets dans logs/snipe.log\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
