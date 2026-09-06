"""`python -m scanner.test_query "nike trail"` — une requête, toutes les sources.

La preuve visible que le moteur est bien multi-source : le total est la
SOMME des sources, jamais le compte d'une seule.

    [Mercari]     12 results
    [Rakuma]       8 results
    [Auction]     14 results
    [Fleamarket]   5 results
    Total:        39 results
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from ._common import (
    BOLD, DIM, KO, OK, RESET, SKIP, build_registry, header, load_settings,
    quiet_logging,
)


async def _run(args) -> int:
    from buyee_radar.platforms import BuyeeSearchEngine

    settings = load_settings(args.config)
    registry = build_registry(settings, only=args.source or None, demo=args.demo)
    engine = BuyeeSearchEngine(registry, affiliate_id=settings.buyee.affiliate_id)

    header(f'Searching Buyee sources for "{args.query}"…')
    await registry.start()
    try:
        report = await engine.search(
            args.query, sources=args.source or None, limit=args.limit
        )
    finally:
        await registry.stop()

    width = max((len(o.label) for o in report.outcomes), default=10) + 2
    for outcome in report.queried:
        mark = OK if outcome.ok else KO
        detail = f"{outcome.count} results  {DIM}{outcome.latency_ms} ms{RESET}"
        if not outcome.ok:
            detail = f"{outcome.error or 'échec'}"
        print(f"  {mark} [{outcome.label}]".ljust(width + 8) + detail)
    for outcome in report.skipped:
        print(f"  {SKIP} [{outcome.label}]".ljust(width + 8)
              + f"{DIM}{outcome.skipped_reason}{RESET}")

    print()
    # Le total vient des annonces attribuées, pas de la somme des compteurs
    # bruts : le cross-search et un namespace dédié peuvent ramener la même
    # annonce, et elle ne doit être comptée qu'une fois.
    unique = {listing.key for listing in report.listings}
    print(f"{BOLD}Total: {len(unique)} results{RESET}"
          f"  {DIM}({len(report.listings)} bruts, "
          f"{len(report.listings) - len(unique)} doublons inter-sources){RESET}")
    print()

    per_source = report.per_source
    if per_source:
        print("Après attribution :")
        cell = max(len(registry.spec(s).label if registry.spec(s) else s)
                   for s in per_source) + 3
        for source, count in sorted(per_source.items(), key=lambda kv: -kv[1]):
            spec = registry.spec(source)
            print(f"  {(spec.label if spec else source):<{cell}}{count:>5}")
        print()

    if args.show:
        for listing in report.listings[: args.show]:
            spec = registry.spec(listing.source)
            label = spec.label if spec else listing.source
            print(f"  {label:<{cell if per_source else 24}}"
                  f"¥{listing.price:<10,}{listing.title[:56]}")
        print()
    return 0 if unique else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scanner.test_query",
        description="Lance une recherche sur TOUTES les sources activées.",
    )
    parser.add_argument("query", help='ex. "nike trail"')
    parser.add_argument("-c", "--config", default=None)
    parser.add_argument("-s", "--source", action="append",
                        help="limiter à cette source (répétable)")
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--show", type=int, default=10,
                        help="afficher les N premières annonces (0 pour aucune)")
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
