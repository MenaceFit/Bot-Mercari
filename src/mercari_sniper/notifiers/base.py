"""Contrat commun aux notificateurs."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..models import Listing


@runtime_checkable
class Notifier(Protocol):
    name: str

    async def start(self) -> None:
        ...

    async def stop(self) -> None:
        ...

    def notify(self, listing: Listing) -> None:
        """Doit être non bloquant : appelé depuis la boucle de détection."""
        ...

    def stats(self) -> dict[str, Any]:
        ...
