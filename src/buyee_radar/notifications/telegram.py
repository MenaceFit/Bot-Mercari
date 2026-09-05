"""Notifications Telegram.

Le jeton n'est JAMAIS écrit dans le code ni dans `radar.yaml`. Il est lu
depuis l'environnement (ou `.env`, git-ignoré) — voir `config/settings.py`.
Un jeton de bot Telegram donne le contrôle total du bot à qui le possède ;
le committer par mégarde le rend révocable seulement, jamais récupérable.

Deux modes d'envoi :

* **avec photo** (`sendPhoto`) — la vignette dit en un coup d'œil si la pièce
  vaut le clic. La légende est plafonnée à 1024 caractères par l'API.
* **sans photo** (`sendMessage`) — repli automatique si l'annonce n'a pas
  d'image, ou si Telegram refuse l'URL de la vignette (ce qui arrive quand
  le CDN de la marketplace bloque les requêtes de Telegram).

Le repli n'est pas un détail : sans lui, une annonce à vignette cassée ne
serait jamais notifiée du tout.
"""

from __future__ import annotations

import html
import logging
import time

import httpx

from ..adapters.base import Listing

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"
#: Plafond de légende imposé par l'API Telegram pour sendPhoto.
CAPTION_LIMIT = 1024
MESSAGE_LIMIT = 4096


class TelegramError(RuntimeError):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class TelegramNotifier:
    """Envoi vers un chat Telegram, avec repli texte si la photo échoue."""

    name = "telegram"

    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        enabled: bool = True,
        send_photo: bool = True,
        timeout: float = 8.0,
        silent: bool = False,
    ) -> None:
        self.token = token
        self.chat_id = str(chat_id)
        self.send_photo = send_photo
        self.silent = silent
        # Désactivé si mal configuré : mieux vaut un canal inactif et un
        # avertissement clair qu'un worker qui échoue à chaque annonce.
        self.enabled = bool(enabled and token and chat_id)
        if enabled and not self.enabled:
            log.warning(
                "Telegram désactivé : TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID "
                "manquant dans .env"
            )
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=4.0),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
        )

    # ── Mise en forme ─────────────────────────────────────────────────────
    def format(self, listing: Listing) -> str:
        """Message HTML. Tout ce qui vient de la marketplace est échappé.

        Un titre d'annonce est du texte contrôlé par un vendeur inconnu :
        sans échappement, un « < » suffit à faire rejeter le message par
        l'API, et une balise bien placée casserait la mise en forme.
        """
        title = html.escape(listing.title[:180])
        keyword = html.escape(listing.keyword or ", ".join(listing.keywords[:2]))
        source = html.escape(listing.source)
        price = f"{listing.price:,}".replace(",", " ")

        lines = [
            "🔥 <b>NOUVELLE ANNONCE</b>",
            "",
            f"<b>{title}</b>",
            "",
            f"💰 <b>{price} {listing.currency}</b>",
            f"🇯🇵 Source : {source}",
        ]
        if keyword:
            lines.append(f"🔎 Mot-clé : {keyword}")

        # La latence de détection n'est affichée que si elle est RÉELLEMENT
        # connue. Beaucoup de sources ne datent pas la publication ; afficher
        # « détectée en 0,0 s » serait une fausse promesse.
        if listing.latency_ms:
            lines.append(f"⚡ Détectée {listing.latency_ms / 1000:.1f} s "
                         f"après publication")
        elif listing.network_ms:
            lines.append(f"⚡ Réponse source en {listing.network_ms} ms")

        if listing.buy_url:
            lines.append("")
            lines.append(f'🛒 <a href="{html.escape(listing.buy_url)}">Commander via Buyee</a>')
        if listing.url:
            lines.append(f'🔗 <a href="{html.escape(listing.url)}">Voir l\'annonce</a>')
        return "\n".join(lines)

    # ── Envoi ─────────────────────────────────────────────────────────────
    async def send(self, listing: Listing) -> None:
        text = self.format(listing)

        if self.send_photo and listing.image_url:
            try:
                await self._call("sendPhoto", {
                    "chat_id": self.chat_id,
                    "photo": listing.image_url,
                    "caption": text[:CAPTION_LIMIT],
                    "parse_mode": "HTML",
                    "disable_notification": self.silent,
                })
                return
            except TelegramError as exc:
                if exc.retry_after is not None:
                    raise      # 429 : c'est au hub de temporiser
                # Vignette refusée (CDN bloqué, URL expirée) : on ne perd pas
                # l'annonce pour autant, on bascule en message texte.
                log.debug("photo refusée, repli texte : %s", exc)

        await self._call("sendMessage", {
            "chat_id": self.chat_id,
            "text": text[:MESSAGE_LIMIT],
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
            "disable_notification": self.silent,
        })

    async def _call(self, method: str, payload: dict) -> dict:
        url = API.format(token=self.token, method=method)
        try:
            response = await self._client.post(url, json=payload)
        except httpx.HTTPError as exc:
            raise TelegramError(f"réseau : {exc}") from exc

        if response.status_code == 429:
            # Telegram indique lui-même combien attendre : le respecter est
            # plus efficace et plus poli qu'un recul exponentiel aveugle.
            retry = 5.0
            try:
                retry = float(
                    (response.json().get("parameters") or {}).get("retry_after", 5)
                )
            except Exception:
                pass
            raise TelegramError("limite de débit Telegram (429)", retry_after=retry)

        if response.status_code >= 400:
            detail = response.text[:200]
            raise TelegramError(f"HTTP {response.status_code} : {detail}")

        data = response.json()
        if not data.get("ok"):
            raise TelegramError(str(data.get("description") or "réponse non ok"))
        return data

    async def verify(self) -> tuple[bool, str]:
        """Teste le jeton et l'accès au chat. Utilisé par `doctor`.

        Vérifier les deux séparément importe : un jeton valide avec un
        `chat_id` faux est le cas le plus courant, et il ne se voit qu'à la
        première annonce si on ne le teste pas.
        """
        if not self.enabled:
            return False, "non configuré"
        try:
            me = await self._call("getMe", {})
        except TelegramError as exc:
            return False, f"jeton invalide : {exc}"

        username = (me.get("result") or {}).get("username", "?")
        try:
            await self._call("getChat", {"chat_id": self.chat_id})
        except TelegramError as exc:
            return False, (
                f"bot @{username} joignable, mais le chat {self.chat_id} ne "
                f"répond pas ({exc}). Envoie-lui d'abord un message depuis "
                f"Telegram, sinon il n'a pas le droit de t'écrire."
            )
        return True, f"@{username} → chat {self.chat_id}"

    async def send_test(self) -> None:
        await self._call("sendMessage", {
            "chat_id": self.chat_id,
            "text": "✅ <b>Snipe</b> — canal Telegram opérationnel.",
            "parse_mode": "HTML",
        })

    async def close(self) -> None:
        await self._client.aclose()
