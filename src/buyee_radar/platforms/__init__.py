"""Buyee en tant que plateforme cible, et ses marketplaces en tant que sources.

    MERCARI IS A SOURCE. BUyee IS THE TARGET SEARCH ECOSYSTEM.
"""

from .adapters import ADAPTERS, BuyeeSourceAdapter, build, source_of_url
from .engine import (
    BuyeeSearchEngine,
    BuyeeSearchReport,
    SourceOutcome,
    SourceRegistry,
)
from .registry import SOURCES, BuyeeSource, all_sources, get, resolve

__all__ = [
    "ADAPTERS", "SOURCES", "BuyeeSearchEngine", "BuyeeSearchReport",
    "BuyeeSource", "BuyeeSourceAdapter", "SourceOutcome", "SourceRegistry",
    "all_sources", "build", "get", "resolve", "source_of_url",
]
