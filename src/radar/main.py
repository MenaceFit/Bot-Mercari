"""Radar Mercari — ligne de commande.

    radar run             scanner + dashboard
    radar run --demo      flux simulé, aucune requête réseau
    radar run --dry-run   détecte et affiche, n'envoie rien
    radar once            un seul cycle, puis sortie
    radar health          l'API Mercari répond-elle ?
    radar notify-test     envoie une annonce d'exemple sur Telegram
    radar doctor          diagnostic de l'installation
    radar init            crée radar.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import platform
import signal
import sys
import time
from pathlib import Path

BANNER = r"""
   ██████╗ ██╗   ██╗██╗   ██╗███████╗███████╗
   ██╔══██╗██║   ██║╚██╗ ██╔╝██╔════╝██╔════╝
   ██████╔╝██║   ██║ ╚████╔╝ █████╗  █████╗      R A D A R
   ██╔══██╗██║   ██║  ╚██╔╝  ██╔══╝  ██╔══╝
   ██████╔╝╚██████╔╝   ██║   ███████╗███████╗   multi-marketplace
   ╚═════╝  ╚═════╝    ╚═╝   ╚══════╝╚══════╝   real-time scanner
"""

REQUIRED = {
    "httpx": "httpx[http2]",
    "yaml": "pyyaml",
    "selectolax": "selectolax",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn[standard]",
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


class JSONFormatter(logging.Formatter):
    """Logs structurés (§33). Une ligne = un objet JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.localtime(record.created)
            ) + f".{int(record.msecs):03d}",
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for field in ("source", "listing_id", "latency_ms", "keyword"):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO", json_logs: bool = False) -> Path | None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(
        JSONFormatter() if json_logs else logging.Formatter(
            "%(asctime)s.%(msecs)03d %(levelname)-7s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root.addHandler(console)

    try:
        Path("logs").mkdir(exist_ok=True)
        # Le fichier est TOUJOURS en JSON : c'est lui qu'on relit ou qu'on
        # envoie à un agrégateur, pas la console.
        file_handler = logging.FileHandler("logs/radar.jsonl", encoding="utf-8")
        file_handler.setFormatter(JSONFormatter())
        root.addHandler(file_handler)
        return Path("logs/radar.jsonl")
    except OSError as exc:
        root.warning("journal fichier indisponible (%s) — console uniquement", exc)
        return None


# ── run ───────────────────────────────────────────────────────────────────
async def _run(args) -> int:
    from .api.server import AppContext, DashboardServer, create_app
    from .app import (
        build_adapters, build_hub, build_scanner,
    )
    from .config.loader import Settings
    from .core.event_bus import EventBus
    from .core.metrics import Metrics
    from .storage.database import Database

    settings = Settings.load(args.config)
    if args.budget:
        settings.scanner.budget_per_second = args.budget
    if args.verbose:
        settings.log_level = "DEBUG"

    log_path = setup_logging(settings.log_level, json_logs=args.json_logs)
    log = logging.getLogger("radar")

    database = Database(settings.storage.database)
    await database.open()

    bus = EventBus()
    metrics = Metrics()
    hub = build_hub(
        settings, dry_run=args.dry_run, metrics=metrics,
        on_sent=lambda listing, channel, ok, ms, err:
            database.queue_notification(listing.key, channel, ok, ms, err),
    )
    await hub.start()

    # Le registre fait autorité sur « quelles sources existent » ; les
    # adapters disent lesquelles tournent. Les deux viennent de la même
    # source de vérité, donc le dashboard ne peut pas afficher un compte
    # qui diverge de ce qui est réellement interrogé.
    adapters = build_adapters(settings, demo=args.demo)
    scanner = build_scanner(settings, adapters, database, hub, bus, metrics=metrics)

    if args.dry_run:
        log.warning("MODE SIMULATION : détections affichées, rien n'est envoyé")
    if args.demo:
        log.warning("MODE DÉMO : sources simulées, aucune requête vers l'extérieur")
    if not settings.keywords:
        log.warning(
            "aucun mot-clé dans %s — le scanner tournera sans rien chercher",
            settings.path,
        )

    await scanner.start()

    context = AppContext(settings, database, bus, scanner, adapters)
    server = None
    if not args.no_api:
        server = DashboardServer(
            create_app(context), settings.api_host, settings.api_port
        )
        await server.start()
        port = server.port
        if server.moved:
            print(f"\n  [!] Le port {settings.api_port} était occupé — "
                  f"dashboard sur {port} à la place.")
            print("      (un autre radar tourne peut-être déjà)")
        print(f"\n  ➜  Dashboard  http://127.0.0.1:{port}/")
        print(f"  ➜  API        http://127.0.0.1:{port}/api/state")

    if args.once:
        await asyncio.sleep(args.once_seconds)
    else:
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

        for report in await scanner.health_check():
            marker = "OK" if report["ok"] else "INDISPONIBLE"
            log.info(
                "source %-16s %-13s %s [%s]",
                report["source"], marker, report["detail"][:70], report["support"],
            )

        print(f"\n  Ctrl+C pour arrêter.  Journal : {log_path or 'console'}\n")
        status = asyncio.create_task(
            _status_loop(scanner, settings.scanner.status_every, stop)
        )
        await stop.wait()
        status.cancel()

    await scanner.stop()
    await hub.drain()
    await hub.stop()
    if server is not None:
        await server.stop()
    await database.close()
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


def cmd_once(args) -> int:
    args.once = True
    args.no_api = True
    return cmd_run(args)


# ── health ────────────────────────────────────────────────────────────────
async def _health(args) -> int:
    from .app import build_adapters
    from .config.loader import Settings

    settings = Settings.load(args.config)
    adapters = build_adapters(settings, demo=args.demo)
    if not adapters:
        print("\n  Aucune source active.\n")
        return 1

    # Largeur calculée : un nom de source long ne doit pas venir coller
    # la colonne suivante.
    width = max(18, max(len(n) for n in adapters) + 2)
    print(f"\n  {'SOURCE':<{width}}{'ÉTAT':<14}{'LATENCE':>10}   SUPPORT")
    print("  " + "─" * (width + 50))
    worst = 0
    for name, adapter in adapters.items():
        await adapter.start()
        try:
            health = await adapter.health_check()
        except Exception as exc:
            print(f"  {name:<{width}}{'EXCEPTION':<14}{'—':>10}   {exc}")
            worst = 1
            continue
        finally:
            await adapter.stop()

        state = "ONLINE" if health.ok else "INDISPONIBLE"
        latency = f"{health.latency_ms} ms" if health.latency_ms else "—"
        print(f"  {name:<{width}}{state:<14}{latency:>10}   {health.support.value}")
        if health.detail:
            print(f"  {'':<{width}}{health.detail[:60]}")
        if not health.ok:
            worst = 1
    print()
    return worst


def cmd_health(args) -> int:
    setup_logging("WARNING")
    return asyncio.run(_health(args))


# ── benchmark ─────────────────────────────────────────────────────────────
def cmd_benchmark(args) -> int:
    missing = check_dependencies()
    if missing:
        report_missing(missing)
        return 1
    from . import benchmark
    from .app import build_keywords
    from .config.loader import Settings

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
    report = asyncio.run(benchmark.run(keywords, duration=args.duration))
    print(benchmark.render(report))
    return 0


# ── doctor / init ─────────────────────────────────────────────────────────
def cmd_doctor(args) -> int:
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

    config_path = Path(args.config or "radar.yaml")
    if not config_path.exists():
        print(f"  [!] {config_path} absent — lance « radar init »")
        ok = False
    else:
        try:
            from .config.loader import Settings

            settings = Settings.load(config_path)
            active = [k for k in settings.keywords if k.enabled]
            if active:
                print(f"  [OK] {config_path} : {len(active)} mot(s)-clé(s) actif(s)")
            else:
                print(f"  [!] {config_path} : aucun mot-clé actif — "
                      f"le scanner tournera sans rien chercher")
                ok = False

            if settings.telegram_token and settings.telegram_chat_id:
                print("  [OK] Telegram configuré dans .env")
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


async def _notify_test(args) -> int:
    """Envoie une annonce d'exemple sur les canaux configurés.

    Vérifier le canal AVANT de lancer le scanner évite le scénario le plus
    frustrant : le bot tourne, il détecte, et rien n'arrive — parce que le
    bot n'est pas administrateur du canal, ou que le chat_id est celui d'un
    autre salon.
    """
    from .adapters.base import Listing
    from .app import build_hub
    from .config.loader import Settings

    settings = Settings.load(args.config)
    n = settings.notifications

    print()
    if n.telegram_enabled and not (settings.telegram_token and settings.telegram_chat_id):
        print("  [X] Telegram activé mais TELEGRAM_BOT_TOKEN ou "
              "TELEGRAM_CHAT_ID manque dans .env\n")
        return 1
    # La cible est affichée par le notifieur lui-même : c'est lui qui sait
    # ce qu'il va réellement viser, y compris un sujet ou un fil précis.
    from .notifications.discord import DiscordNotifier
    from .notifications.telegram import TelegramNotifier

    if n.telegram_enabled:
        probe = TelegramNotifier(
            settings.telegram_token, settings.telegram_chat_id,
            topic_id=settings.telegram_topic_id,
        )
        print(f"  Telegram : {probe.target_label}")
        print(f"             format « {n.telegram_style} »")
        if not settings.telegram_chat_id.lstrip("-").isdigit():
            print("             le bot doit être ADMINISTRATEUR du canal")
        if settings.telegram_topic_id and not settings.telegram_chat_id.startswith("-"):
            print("             [!] un sujet n'existe que dans un GROUPE Forum ;")
            print("                 cet identifiant ne ressemble pas à un groupe")
    if n.discord_enabled:
        probe = DiscordNotifier(
            settings.discord_webhook, thread_id=settings.discord_thread_id,
        )
        print(f"  Discord  : {probe.target_label}")
    if not (n.telegram_enabled or n.discord_enabled):
        print("  Aucun canal activé dans radar.yaml (notifications:).\n")
        return 1

    sample = Listing(
        source="mercari",
        listing_id="test",
        title="NIKE ACG トレイル ジャケット 新品未使用  (message de test)",
        url="https://jp.mercari.com/item/m00000000000",
        buy_url="https://jp.mercari.com/item/m00000000000",
        price=12500,
        score=78,
        tier="VERY RARE",
        keyword="Nike ACG",
    )
    sample.price_eur = settings.currency.fixed_rate * 12500 if settings.currency.fixed_rate else 76.0

    hub = build_hub(settings, dry_run=args.dry_run)
    await hub.start()
    hub.dispatch(sample)
    # Laisser la file se vider : l'envoi est asynchrone par conception, et
    # le scanner ne l'attend jamais. Ici, si.
    await hub.drain(timeout=15.0)
    await hub.stop()

    print()
    ok = True
    for channel, stats in hub.report().items():
        sent, failed = stats.get("sent", 0), stats.get("failed", 0)
        mark = "OK" if sent and not failed else "X"
        print(f"  [{mark}] {channel:10} {sent} envoyé(s), {failed} échec(s)")
        if failed or not sent:
            ok = False
            if stats.get("last_error"):
                print(f"       {stats['last_error']}")
    print()
    if not ok:
        print("  Pistes : as-tu écrit au moins une fois à ton bot ?")
        print("           Est-il ADMINISTRATEUR du canal ?")
        print("           Le chat_id est-il bien celui du canal (@nom ou -100…) ?\n")
        return 1
    print("  Regarde la destination configurée ci-dessus.\n")
    return 0


def cmd_notify_test(args) -> int:
    return asyncio.run(_notify_test(args))


def cmd_init(args) -> int:
    target = Path(args.config or "radar.yaml")
    if target.exists() and not args.force:
        print(f"\n  {target} existe déjà. Utilise --force pour l'écraser.\n")
        return 1
    example = Path(__file__).parent / "config.example.yaml"
    if not example.exists():
        print(f"\n  Modèle introuvable : {example}\n")
        return 1
    target.write_text(example.read_text("utf-8"), encoding="utf-8")
    print(f"\n  {target} créé.")
    print("  1. Ajoute tes mots-clés")
    print("  2. radar doctor          (vérifie l'installation)")
    print("  3. radar run --demo     (essai sans réseau)\n")
    return 0


# ── argparse ──────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="radar",
        description="Sniper Mercari Japon — détection temps réel des nouvelles annonces.",
    )
    parser.add_argument("-c", "--config", default="radar.yaml")
    sub = parser.add_subparsers(dest="command")

    def add_config(p):
        """`-c` doit marcher des DEUX côtés du sous-commande.

        « radar run -c radar.yaml » est ce qu'on tape naturellement ;
        refuser cette forme pour une raison d'argparse est gratuit.
        """
        p.add_argument("-c", "--config", default=None,
                       help="fichier de configuration (défaut : radar.yaml)")

    def add_run_options(p):
        p.add_argument("--demo", action="store_true",
                       help="sources simulées, aucune requête réseau")
        p.add_argument("--dry-run", action="store_true",
                       help="détecte et affiche, n'envoie aucune notification")
        p.add_argument("--budget", type=float, help="requêtes/seconde, toutes sources")
        p.add_argument("--no-api", action="store_true", help="scanner seul, sans API")
        p.add_argument("--json-logs", action="store_true", help="logs JSON en console")
        p.add_argument("-v", "--verbose", action="store_true")

    run = sub.add_parser("run", help="scanner + API")
    add_config(run)
    add_run_options(run)
    run.add_argument("--once", action="store_true")
    run.add_argument("--once-seconds", type=float, default=15.0)
    run.set_defaults(func=cmd_run)

    once = sub.add_parser("once", help="un seul cycle, puis sortie")
    add_config(once)
    add_run_options(once)
    once.add_argument("--once-seconds", type=float, default=15.0)
    once.set_defaults(func=cmd_once)


    health = sub.add_parser("health", help="état de chaque source")
    add_config(health)
    health.add_argument("--demo", action="store_true")
    health.set_defaults(func=cmd_health)

    bench = sub.add_parser("benchmark", help="latences par étape")
    add_config(bench)
    bench.add_argument("--duration", type=float, default=8.0)
    bench.set_defaults(func=cmd_benchmark)

    doctor = sub.add_parser("doctor", help="diagnostic de l'installation")
    add_config(doctor)
    doctor.set_defaults(func=cmd_doctor)

    notify = sub.add_parser(
        "notify-test", help="envoie une annonce d'exemple sur Telegram/Discord"
    )
    add_config(notify)
    notify.add_argument("--dry-run", action="store_true",
                        help="affiche le message sans l'envoyer")
    notify.set_defaults(func=cmd_notify_test)

    init = sub.add_parser("init", help="crée radar.yaml")
    add_config(init)
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    return parser


def _default_to_run(argv: list[str]) -> list[str]:
    """`radar --demo` doit marcher comme `radar run --demo`."""
    commands = {"run", "once", "health", "benchmark", "doctor",
                "init", "notify-test"}
    if any(arg in commands for arg in argv):
        return argv
    if any(arg in ("-h", "--help") for arg in argv):
        return argv

    globals_, index = [], 0
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
        break
    return globals_ + ["run"] + argv[index:]


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

    # argparse écrase la valeur globale par celle du sous-parser (None si
    # l'utilisateur ne l'a pas donnée là). On rétablit la bonne priorité :
    # ce qui a été tapé gagne, quel que soit le côté.
    if getattr(args, "config", None) is None:
        for token in ("-c", "--config"):
            if token in argv:
                index = argv.index(token)
                if index + 1 < len(argv):
                    args.config = argv[index + 1]
                break
        else:
            equals = [a for a in argv if a.startswith("--config=")]
            args.config = equals[0].split("=", 1)[1] if equals else "radar.yaml"

    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\n  Arrêté.")
        return 0
    except SystemExit as exc:
        # uvicorn appelle sys.exit() quand il ne peut pas démarrer. Laissé
        # passer, ça referme la console sur une trace Python.
        code = int(exc.code or 0)
        if code:
            print(f"\n  Le serveur n'a pas pu démarrer (code {code}).")
            print("  Vérifie qu'aucun autre radar ne tourne, ou change "
                  "« api_port » dans radar.yaml.\n")
        return code
    except Exception:
        # Rien ne doit remonter : sur un double-clic Windows, une exception
        # non attrapée ferme la console avec la trace dedans.
        import traceback
        print("\n  Erreur inattendue :\n")
        traceback.print_exc()
        print("\n  Détails complets dans logs/radar.jsonl\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
