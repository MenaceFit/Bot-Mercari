"""Backends de recherche interchangeables."""

from .base import BackendError, SearchBackend, SearchQuery
from .mercari_api import MercariAPIBackend
from .simulator import SimulatorBackend

__all__ = [
    "BackendError",
    "SearchBackend",
    "SearchQuery",
    "MercariAPIBackend",
    "SimulatorBackend",
]
