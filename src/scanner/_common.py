"""Ce que les trois commandes de test partagent."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Le dépôt est en layout src/ : permettre « python -m scanner.… » sans
# `pip install -e .` préalable, comme le fait déjà main.py à la racine.
_SRC = Path(__file__).resolve().parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

GREEN, RED, DIM, BOLD, YELLOW, RESET = (
    "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[33m", "\033[0m"
)
if not sys.stdout.isatty():
    GREEN = RED = DIM = BOLD = YELLOW = RESET = ""

OK, KO, SKIP = f"{GREEN}✓{RESET}", f"{RED}✗{RESET}", f"{DIM}·{RESET}"


def quiet_logging(level: str = "WARNING") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.WARNING),
        format="%(message)s",
    )


def load_settings(path: str | None):
    from buyee_radar.config.loader import Settings

    return Settings.load(path)


def build_registry(settings, *, only: list[str] | None = None,
                   include_unsupported: bool = True, demo: bool = False):
    """Le registre tel que radar.yaml le décrit.

    `only` force l'activation de sources précises — utile pour tester une
    source que la configuration a désactivée.
    """
    from buyee_radar.buyee import SourceRegistry
    from buyee_radar.buyee.registry import resolve

    specs = {
        name: {
            "enabled": spec.enabled,
            "selectors": dict(spec.selectors or {}),
            "search_url": spec.search_url,
            "extra_params": dict(spec.extra_params or {}),
            "max_concurrent": spec.max_concurrent,
            "connect_timeout": spec.connect_timeout,
            "read_timeout": spec.read_timeout,
            "total_timeout": spec.total_timeout,
            "max_retries": spec.max_retries,
        }
        for name, spec in settings.sources.items()
        if not name.startswith("sim_")
    }
    registry = SourceRegistry.build(
        specs, include_unsupported=include_unsupported
    )
    if demo:
        # Les simulateurs entrent dans le MÊME registre et passent par le
        # MÊME moteur : c'est ce qui rend la démonstration probante. Leur
        # nom commence par « sim_ », visible partout — rien n'est déguisé
        # en source réelle.
        from buyee_radar.adapters.simulator import PROFILES, SimulatorAdapter

        for index, name in enumerate(PROFILES):
            registry.add(SimulatorAdapter(name, seed=1234 + index), enabled=True)
    for name in only or []:
        registry.enable(resolve(name), True)
    return registry


def header(title: str) -> None:
    print()
    print(f"{BOLD}{title}{RESET}")
    print("─" * max(28, len(title)))
