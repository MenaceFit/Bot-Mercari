"""Contrat commun à tous les backends de recherche."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..models import Listing


@dataclass(slots=True)
class SearchQuery:
    """Une requête de recherche, indépendante du backend."""

    keyword: str
    page_size: int = 120
    min_price: int | None = None
    max_price: int | None = None
    exclude_keyword: str = ""
    categories: list[int] = field(default_factory=list)
    brands: list[int] = field(default_factory=list)
    item_conditions: list[int] = field(default_factory=list)


class BackendError(RuntimeError):
    """Échec de recherche. `retry_after` renseigné sur un 429."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after

    @property
    def is_rate_limit(self) -> bool:
        return self.status == 429

    @property
    def is_blocked(self) -> bool:
        """403/451 : blocage applicatif, pas une simple saturation."""
        return self.status in (403, 451)


@runtime_checkable
class SearchBackend(Protocol):
    """Source d'annonces interchangeable (API réelle, simulateur, ...)."""

    name: str

    async def search(self, query: SearchQuery) -> list[Listing]:
        """Renvoie les annonces les plus récentes, triées du plus neuf au plus vieux."""
        ...

    async def aclose(self) -> None:
        ...
