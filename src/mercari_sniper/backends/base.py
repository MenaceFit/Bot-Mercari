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
    # Jeton de pagination : permet de remonter au-delà de la première page
    # quand on détecte qu'on a raté des annonces entre deux scans.
    page_token: str = ""


@dataclass(slots=True)
class SearchPage:
    """Une page de résultats, avec de quoi remonter à la suivante."""

    items: list[Listing]
    next_page_token: str = ""

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self):
        return iter(self.items)


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

    async def search(self, query: SearchQuery) -> SearchPage:
        """Renvoie une page d'annonces, de la plus récente à la plus ancienne."""
        ...

    async def aclose(self) -> None:
        ...
