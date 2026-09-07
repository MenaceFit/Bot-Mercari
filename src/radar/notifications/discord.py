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
        username: str = "Radar Mercari",
        thread_id: str | int | None = None,
    ) -> None:
        # Un webhook est déjà lié à UN salon : c'est le salon qu'on a choisi
        # en le créant. `thread_id` sert à viser un FIL à l'intérieur de ce
        # salon — ou un post de forum, qui en est un cas particulier.
        self.webhook_url = self._with_thread(webhook_url, thread_id)
        self.thread_id = str(thread_id).strip() if thread_id else ""
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

    @staticmethod
    def _with_thread(url: str, thread_id: str | int | None) -> str:
        if not (url and thread_id):
            return url
        value = str(thread_id).strip()
        if not value or f"thread_id={value}" in url:
            return url
        return url + ("&" if "?" in url else "?") + f"thread_id={value}"

    @property
    def target_label(self) -> str:
        """Le salon n'est pas nommable ici : le webhook seul le connaît."""
        if self.thread_id:
            return f"salon du webhook, fil #{self.thread_id}"
        return "salon du webhook"

    def build_embed(self, listing: Listing) -> dict:
        fields = [
            {
                "name": "Prix",
                # L'euro d'abord — c'est le chiffre qu'on compare à ce
                # qu'on est prêt à payer. Rien d'inventé si le taux manque.
                "value": (
                    f"**{listing.price_eur:,.0f} €** · {listing.price:,} ¥"
                    if listing.price_eur > 0
                    else f"**{listing.price:,}** {listing.currency}"
                ).replace(",", " "),
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
                "value": f"[Voir sur Mercari]({listing.buy_url})",
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
