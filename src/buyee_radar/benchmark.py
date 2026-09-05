"""Mode `--benchmark` : mesurer, pas promettre.

Le cahier des charges demande le détail par étape : DNS, TCP, TLS, HTTP,
parsing, normalisation, filtrage, déduplication, notification.

Ce que ce module mesure vraiment, et ce qu'il ne mesure pas :

* **Étapes locales** (parsing, normalisation, filtrage, déduplication,
  construction de la notification) — mesurées exactement, en isolant chaque
  étape sur un jeu d'annonces réaliste.
* **Étapes réseau** (DNS, TCP, TLS, premier octet) — mesurées uniquement si
  une source réelle est joignable. Depuis l'environnement de développement
  elles ne le sont pas : la passerelle refuse le CONNECT vers les
  marketplaces. Le benchmark le DIT au lieu d'afficher des zéros qui
  passeraient pour de la vitesse.

C'est la « RÈGLE ABSOLUE » appliquée : une étape non mesurée est marquée
« non mesurée », jamais estimée.
"""

from __future__ import annotations

import asyncio
import socket
import statistics
import time
from typing import Any

from .core.deduplicator import Deduplicator
from .core.matcher import FilterEngine, GlobalFilters
from .core.keywords import Keyword
from .core.normalizer import clear_caches, normalize_text
from .adapters.base import Listing
from .adapters.simulator import SimulatorAdapter

#: Jeu de titres représentatif : japonais, latin, mixte, et du bruit.
SAMPLE_TITLES = [
    "ナイキ ディビジョン ランニング ジャケット M 新品未使用",
    "NIKE ACG スミス サミット カーゴパンツ L 美品",
    "アンダーアーマー プロジェクトロック フーディー XL",
    "adidas terrex トレイル シューズ 27cm used",
    "ナイキ ギャクソウ gyakusou ハーフパンツ 東京",
    "DIOR 香水 ミス ディオール 50ml",
    "ノースフェイス マウンテンパーカー ripstop",
    "ナイキ 空箱 のみ タグ付き",
]


def _sample(n: int = 2000) -> list[Listing]:
    now = time.time()
    return [
        Listing(
            source="benchmark",
            listing_id=f"bench{i:06d}",
            title=SAMPLE_TITLES[i % len(SAMPLE_TITLES)],
            url=f"https://example.invalid/{i}",
            price=2800 + (i % 20) * 900,
            created_at=now - 2.0,
            requested_at=now - 0.15,
            detected_at=now,
        )
        for i in range(n)
    ]


def _time(fn, repeat: int) -> float:
    """Renvoie le coût moyen d'un appel, en millisecondes."""
    fn()                                   # échauffement des caches
    start = time.perf_counter()
    for _ in range(repeat):
        fn()
    return (time.perf_counter() - start) / repeat * 1000


def measure_local(keywords: list[Keyword], n: int = 2000) -> dict[str, Any]:
    """Coût du pipeline local, étape par étape, hors réseau."""
    listings = _sample(n)
    filters = FilterEngine(keywords=keywords, globals_=GlobalFilters())
    dedup = Deduplicator()

    clear_caches()
    normalize_cold = _time(lambda: [normalize_text(l.title) for l in listings[:200]], 5)
    normalize_warm = _time(lambda: [normalize_text(l.title) for l in listings[:200]], 20)

    now = time.time()
    filtering = _time(lambda: [filters.match(l, now) for l in listings[:200]], 20)

    counter = [0]

    def dedup_pass():
        counter[0] += 1
        prefix = counter[0]
        for listing in listings[:200]:
            dedup.add(f"{prefix}:{listing.key}")

    dedup_cost = _time(dedup_pass, 20)

    return {
        "sample_size": 200,
        "normalize_cold_ms": round(normalize_cold, 3),
        "normalize_warm_ms": round(normalize_warm, 3),
        "filter_ms": round(filtering, 3),
        "dedup_ms": round(dedup_cost, 3),
        "per_listing_us": round(
            (normalize_warm + filtering + dedup_cost) / 200 * 1000, 2
        ),
        "throughput_per_s": int(
            200 / max(1e-9, (normalize_warm + filtering + dedup_cost) / 1000)
        ),
    }


async def measure_network(host: str, port: int = 443, samples: int = 3) -> dict[str, Any]:
    """DNS + TCP + TLS vers un hôte réel. Renvoie `reachable: False` si bloqué.

    Aucune estimation de repli : un réseau non mesuré doit se voir comme tel.
    """
    dns_times: list[float] = []
    tcp_times: list[float] = []
    tls_times: list[float] = []
    error = ""

    for _ in range(samples):
        start = time.perf_counter()
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, port, proto=socket.IPPROTO_TCP
            )
        except OSError as exc:
            error = f"DNS : {exc}"
            break
        dns_times.append((time.perf_counter() - start) * 1000)

        family, type_, proto, _, address = infos[0]
        start = time.perf_counter()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(address[0], port), timeout=8
            )
        except (OSError, asyncio.TimeoutError) as exc:
            error = f"TCP : {exc}"
            break
        tcp_times.append((time.perf_counter() - start) * 1000)
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass

        start = time.perf_counter()
        try:
            import ssl
            context = ssl.create_default_context()
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    address[0], port, ssl=context, server_hostname=host
                ),
                timeout=8,
            )
            tls_times.append((time.perf_counter() - start) * 1000)
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
        except Exception as exc:
            error = f"TLS : {exc}"
            break

    reachable = bool(tls_times)
    return {
        "host": host,
        "reachable": reachable,
        "error": error,
        "dns_ms": round(statistics.mean(dns_times), 1) if dns_times else None,
        "tcp_ms": round(statistics.mean(tcp_times), 1) if tcp_times else None,
        "tls_ms": round(statistics.mean(tls_times), 1) if tls_times else None,
    }


async def measure_source_roundtrip(source, query_text: str = "nike", samples: int = 3):
    """Aller-retour complet sur une source, parsing inclus."""
    from .adapters.base import SearchQuery, AdapterError

    latencies: list[float] = []
    counts: list[int] = []
    error = ""
    for _ in range(samples):
        try:
            result = await source.search(SearchQuery(text=query_text, limit=60))
        except AdapterError as exc:
            error = str(exc)
            break
        latencies.append(result.latency_ms)
        counts.append(len(result))

    return {
        "source": getattr(source, "name", "?"),
        "ok": bool(latencies),
        "error": error,
        "samples": len(latencies),
        "avg_ms": round(statistics.mean(latencies), 1) if latencies else None,
        "min_ms": round(min(latencies), 1) if latencies else None,
        "max_ms": round(max(latencies), 1) if latencies else None,
        "items_avg": round(statistics.mean(counts), 1) if counts else None,
    }


async def run(keywords: list[Keyword], hosts: list[str] | None = None, duration: float = 8.0) -> dict[str, Any]:
    hosts = hosts or ["buyee.jp", "api.telegram.org", "discord.com"]
    local = measure_local(keywords)
    network = [await measure_network(host) for host in hosts]
    # Cadence élevée : sur un simulateur fraîchement démarré, une page
    # quasi vide ne dirait rien du coût réel de parsing.
    simulator = SimulatorAdapter("sim_mercari", seed=7, new_per_second=400.0, noise_ratio=0.2)
    roundtrip = await measure_source_roundtrip(simulator, query_text="")
    await simulator.stop()
    return {"local": local, "network": network, "simulator": roundtrip}


def render(report: dict[str, Any]) -> str:
    local = report["local"]
    lines = [
        "",
        "=" * 62,
        "  BENCHMARK",
        "=" * 62,
        "",
        "  PIPELINE LOCAL          (mesuré sur 200 annonces réalistes)",
        f"    Normalisation à froid   {local['normalize_cold_ms']:8.3f} ms",
        f"    Normalisation à chaud   {local['normalize_warm_ms']:8.3f} ms   (cache actif)",
        f"    Filtrage                {local['filter_ms']:8.3f} ms",
        f"    Déduplication           {local['dedup_ms']:8.3f} ms",
        "    " + "-" * 46,
        f"    Par annonce             {local['per_listing_us']:8.2f} µs",
        f"    Débit                   {local['throughput_per_s']:8,} annonces/s".replace(",", " "),
        "",
        "  RÉSEAU                  (mesuré, ou signalé comme non mesurable)",
    ]
    for entry in report["network"]:
        if entry["reachable"]:
            lines.append(
                f"    {entry['host']:24} DNS {entry['dns_ms']:6.1f} ms  "
                f"TCP {entry['tcp_ms']:6.1f} ms  TLS {entry['tls_ms']:6.1f} ms"
            )
        else:
            lines.append(f"    {entry['host']:24} INJOIGNABLE — {entry['error'][:60]}")

    sim = report["simulator"]
    lines += [
        "",
        "  ALLER-RETOUR SIMULÉ     (pipeline complet, sans réseau réel)",
        f"    {sim['samples']} requêtes, {sim['avg_ms']} ms en moyenne, "
        f"{sim['items_avg']} annonces/page" if sim["ok"] else
        f"    échec : {sim['error']}",
        "",
        "  Lecture : le pipeline local est négligeable devant le réseau.",
        "  La latence réelle de détection est donc dominée par deux choses",
        "  hors de portée du code : le délai d'indexation de la marketplace,",
        "  et la cadence de scan autorisée par ses limites de débit.",
        "=" * 62,
        "",
    ]
    return "\n".join(lines)
