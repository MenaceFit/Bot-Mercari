"""`python -m scanner.test_live` — 60 secondes de surveillance en direct.

Affiche, ligne par ligne, ce que le scanner détecte réellement :

    09:31:04.128  RAKUMA        Nike Trail Jacket          0.812 s
    09:31:05.421  MERCARI       Nike ACG Vest              0.932 s

La dernière colonne est la latence de détection (T0 → T3) quand la source
date ses annonces. Elle affiche « — » quand ce n'est pas le cas : une
latence inventée serait pire qu'une latence absente.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime

from ._common import BOLD, DIM, RESET, build_registry, header, load_settings, quiet_logging


async def _run(args) -> int:
    from buyee_radar.buyee import BuyeeSearchEngine

    settings = load_settings(args.config)
    registry = build_registry(settings, only=args.source or None, demo=args.demo)
    engine = BuyeeSearchEngine(registry, affiliate_id=settings.buyee.affiliate_id)

    queries = args.query or [
        term for keyword in settings.keywords if keyword.enabled
        for term in (keyword.search or [keyword.name])
    ][:4] or ["nike"]

    usable = registry.usable
    header(f"BUyee Live — {args.duration:.0f} s")
    print(f"{DIM}sources interrogeables : "
          f"{', '.join(a.source for a in usable) or 'aucune'}{RESET}")
    print(f"{DIM}requêtes : {', '.join(queries)}{RESET}")
    print()
    if not usable:
        print("Aucune source interrogeable — lance « buyee-radar calibrate »"
              " puis active-la dans radar.yaml.\n")
        return 1

    # Largeur calculée sur les libellés réellement présents : un nom long
    # comme « sim_jdirectitems_fleamarket » ne doit pas venir coller la
    # colonne suivante.
    width = max(
        (len((registry.spec(a.source).label if registry.spec(a.source)
              else a.source)) for a in usable),
        default=12,
    ) + 2
    total_width = 14 + width + 44 + 10
    print(f"{BOLD}{'HEURE':<14}{'SOURCE':<{width}}{'ANNONCE':<44}{'LATENCE':>10}{RESET}")
    print("─" * total_width)

    seen: set[str] = set()
    per_source: dict[str, int] = {}
    deadline = time.monotonic() + args.duration
    await registry.start()
    try:
        while time.monotonic() < deadline:
            cycle = time.monotonic()
            reports = await asyncio.gather(
                *(engine.search(q, limit=args.limit) for q in queries),
                return_exceptions=True,
            )
            for report in reports:
                if isinstance(report, BaseException):
                    continue
                for listing in report.listings:
                    if listing.key in seen:
                        continue
                    seen.add(listing.key)
                    per_source[listing.source] = per_source.get(listing.source, 0) + 1
                    spec = registry.spec(listing.source)
                    stamp = datetime.fromtimestamp(
                        listing.detected_at or time.time()
                    ).strftime("%H:%M:%S.%f")[:-3]
                    latency = (
                        f"{listing.latency_ms / 1000:.3f} s"
                        if listing.latency_ms else "—"
                    )
                    label = (spec.label if spec else listing.source).upper()
                    print(f"{stamp:<14}{label:<{width}}{listing.title[:42]:<44}"
                          f"{latency:>10}")
            elapsed = time.monotonic() - cycle
            await asyncio.sleep(max(0.0, args.interval - elapsed))
    finally:
        await registry.stop()

    print("─" * total_width)
    print(f"{BOLD}{len(seen)} annonces distinctes en {args.duration:.0f} s{RESET}")
    for source, count in sorted(per_source.items(), key=lambda kv: -kv[1]):
        spec = registry.spec(source)
        print(f"  {(spec.label if spec else source):<{width + 4}}{count:>5}")
    print()
    return 0 if seen else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scanner.test_live",
        description="Surveille les sources Buyee en direct et affiche chaque détection.",
    )
    parser.add_argument("-c", "--config", default=None)
    parser.add_argument("-d", "--duration", type=float, default=60.0)
    parser.add_argument("-i", "--interval", type=float, default=3.0,
                        help="secondes entre deux cycles (défaut : 3)")
    parser.add_argument("-q", "--query", action="append",
                        help="requête à surveiller (répétable ; défaut : les mots-clés)")
    parser.add_argument("-s", "--source", action="append")
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--demo", action="store_true",
                        help="ajoute les sources simulées (sim_*), sans réseau")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    quiet_logging("INFO" if args.verbose else "ERROR")
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\n  Arrêté.\n")
        return 0


if __name__ == "__main__":
    sys.exit(main())
