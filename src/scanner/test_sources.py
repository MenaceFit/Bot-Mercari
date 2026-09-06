"""`python -m scanner.test_sources` — chaque source Buyee, une par une.

Répond à la seule question qui compte : **mon scanner peut-il techniquement
récupérer les résultats de recherche de cette source depuis Buyee ?**
Pas « Buyee supporte-t-il cette plateforme ? » — c'est très différent.

La réponse est obtenue en LANÇANT une vraie recherche, pas en lisant une
table. Une source qui répond 200 avec zéro annonce extraite est un échec,
et c'est affiché comme tel.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from ._common import KO, OK, SKIP, DIM, RESET, build_registry, header, load_settings, quiet_logging


async def _run(args) -> int:
    from buyee_radar.adapters.base import SupportLevel
    from buyee_radar.platforms import BuyeeSearchEngine

    settings = load_settings(args.config)
    registry = build_registry(
        settings, only=args.source or None, include_unsupported=True,
        demo=args.demo,
    )
    engine = BuyeeSearchEngine(registry, affiliate_id=settings.buyee.affiliate_id)

    header("BUyee Source Test")
    await registry.start()
    try:
        report = await engine.search(
            args.keyword, sources=args.source or None, limit=args.limit
        )
    finally:
        await registry.stop()

    usable = 0
    for outcome in report.outcomes:
        spec = registry.spec(outcome.source)
        if not outcome.queried:
            print(f"{SKIP} {outcome.label}")
            print(f"    {DIM}{outcome.skipped_reason}{RESET}")
            if spec and spec.support is SupportLevel.UNSUPPORTED:
                print(f"    {DIM}UNSUPPORTED{RESET}")
            print()
            continue

        if outcome.ok and outcome.count:
            usable += 1
            print(f"{OK} {outcome.label}")
            print(f"    Search:  OK  ({outcome.latency_ms} ms)")
            print(f"    Results: {outcome.count}")
        elif outcome.ok:
            print(f"{KO} {outcome.label}")
            print(f"    Search:  HTTP OK, 0 annonce extraite ({outcome.latency_ms} ms)")
            print(f"    {DIM}la page a répondu mais les sélecteurs n'ont rien trouvé —"
                  f" relance « buyee-radar calibrate --source {outcome.source} »{RESET}")
        else:
            print(f"{KO} {outcome.label}")
            print(f"    Search:  ÉCHEC — {outcome.error or 'sans détail'}")
        print()

    counts = registry.counts()
    print(f"Sources réellement exploitables : {usable}/{counts['total']}")
    print(f"{DIM}interrogées {len(report.queried)} · écartées {len(report.skipped)}"
          f" · {report.elapsed_ms} ms au total{RESET}")
    print()
    return 0 if usable else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scanner.test_sources",
        description="Teste chaque source Buyee par une vraie recherche.",
    )
    parser.add_argument("-c", "--config", default=None)
    parser.add_argument("-k", "--keyword", default="nike",
                        help="mot-clé de test (défaut : nike)")
    parser.add_argument("-s", "--source", action="append",
                        help="limiter à cette source (répétable)")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--demo", action="store_true",
                        help="ajoute les sources simulées (sim_*), sans réseau")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    quiet_logging("INFO" if args.verbose else "ERROR")
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
