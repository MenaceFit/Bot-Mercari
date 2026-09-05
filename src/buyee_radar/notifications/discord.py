"""Notifications Discord, via webhook. Optionnel, désactivé par défaut.

Comme pour Telegram, l'URL du webhook est un secret : elle vaut droit
d'écriture dans le salon pour quiconque la possède. Elle est donc lue depuis
l'environnement uniquement, jamais depuis `radar.yaml`.
"""

from __future__ import annotations

import logging

import httpx

from ..adapters.base import Listing

log = logging.getLogger(__name__)

#: Couleurs de la barre latérale de l'embed. Elles doublent une information
#: déjà écrite en toutes lettres dans le titre — jamais l'inverse.
COLOR_DEFAULT = 0x2A78D6
COLOR_HIGH = 0xE8334A


class DiscordError(RuntimeError):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class DiscordNotifier:
    name = "discord"

    def __init__(
        self,
        webhook_url: str,
        *,
        enabled: bool = True,
        timeout: float = 8.0,
        username: str = "Snipe",
    ) -> None:
        self.webhook_url = webhook_url
        self.username = username
        self.enabled = bool(enabled and webhook_url)
        if enabled and not self.enabled:
            log.warning(
                "Discord désactivé : DISCORD_WEBHOOK_URL manquant dans .env"
            )
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=4.0),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
        )

    def build_embed(self, listing: Listing) -> dict:
        fields = [
            {
                "name": "Prix",
                "value": f"**{listing.price:,}** {listing.currency}".replace(",", " "),
                "inline": True,
            },
            {"name": "Source", "value": listing.source, "inline": True},
        ]
        if listing.keyword or listing.keywords:
            fields.append({
                "name": "Mot-clé",
                "value": listing.keyword or ", ".join(listing.keywords[:3]),
                "inline": True,
            })
        # Uniquement si la donnée existe réellement — voir la note sur les
        # fausses promesses de latence dans `sources/base.py`.
        if listing.latency_ms:
            fields.append({
                "name": "Détection",
                "value": f"{listing.latency_ms / 1000:.1f} s après publication",
                "inline": True,
            })
        if listing.buy_url:
            fields.append({
                "name": "Commander",
                "value": f"[Acheter via Buyee]({listing.buy_url})",
                "inline": False,
            })

        embed = {
            "title": listing.title[:250],
            "url": listing.url or None,
            "color": COLOR_DEFAULT,
            "fields": fields,
        }
        if listing.image_url:
            embed["thumbnail"] = {"url": listing.image_url}
        return embed

    async def send(self, listing: Listing) -> None:
        payload = {
            "username": self.username,
            "embeds": [self.build_embed(listing)],
        }
        try:
            response = await self._client.post(self.webhook_url, json=payload)
        except httpx.HTTPError as exc:
            raise DiscordError(f"réseau : {exc}") from exc

        if response.status_code == 429:
            retry = 2.0
            try:
                retry = float(response.json().get("retry_after", 2))
            except Exception:
                pass
            raise DiscordError("limite de débit Discord (429)", retry_after=retry)
        if response.status_code >= 400:
            raise DiscordError(f"HTTP {response.status_code} : {response.text[:200]}")

    async def close(self) -> None:
        await self._client.aclose()
