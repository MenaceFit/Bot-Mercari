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
        style: str = "clean",
        topic_id: str | int | None = None,
    ) -> None:
        self.token = token
        # Un canal accepte « @nom_du_canal » aussi bien qu'un identifiant
        # numérique « -100… ». Le bot doit y être administrateur.
        self.chat_id = str(chat_id).strip()
        self.send_photo = send_photo
        self.style = style if style in ("clean", "detailed") else "clean"
        # Sujet d'un groupe en mode Forum (« Topics »). Sans lui, tout
        # arrive dans le sujet « General », ce qui noie le salon dédié.
        self.topic_id = self._topic(topic_id)
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

    @staticmethod
    def _topic(value: str | int | None) -> int | None:
        """Identifiant de sujet, ou rien. Une valeur illisible est ignorée.

        Refuser de démarrer pour un sujet mal saisi serait disproportionné :
        le groupe reste joignable, seul le rangement est perdu. On le
        signale et on continue.
        """
        if value in (None, "", 0, "0"):
            return None
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            log.warning(
                "TELEGRAM_TOPIC_ID illisible (« %s ») : les messages iront "
                "dans le sujet général du groupe", value,
            )
            return None

    def _target(self, payload: dict) -> dict:
        """Ajoute la cible fine au corps de la requête."""
        if self.topic_id is not None:
            payload["message_thread_id"] = self.topic_id
        return payload

    @property
    def target_label(self) -> str:
        """Ce que « doctor » et « notify-test » affichent."""
        if self.chat_id.startswith("@"):
            kind = "canal public"
        elif self.chat_id.startswith("-100"):
            kind = "canal ou groupe"
        elif self.chat_id.startswith("-"):
            kind = "groupe"
        else:
            kind = "chat privé"
        label = f"{kind} « {self.chat_id} »"
        if self.topic_id is not None:
            label += f", sujet #{self.topic_id}"
        return label

    # ── Mise en forme ─────────────────────────────────────────────────────
    def format(self, listing: Listing) -> str:
        """Message HTML. Tout ce qui vient de la marketplace est échappé.

        Un titre d'annonce est du texte contrôlé par un vendeur inconnu :
        sans échappement, un « < » suffit à faire rejeter le message par
        l'API, et une balise bien placée casserait la mise en forme.
        """
        if self.style == "detailed":
            return self._detailed(listing)
        return self._clean(listing)

    # Le prix en euros est LE chiffre utile : c'est celui qu'on compare à
    # ce qu'on est prêt à payer. Le yen reste en second, parce que c'est
    # lui qui figure sur la page et qu'il permet de vérifier.
    @staticmethod
    def _prices(listing: Listing) -> str:
        yen = f"{listing.price:,}".replace(",", "\u202f")
        if listing.price_eur > 0:
            euro = f"{listing.price_eur:,.0f}".replace(",", "\u202f")
            return f"<b>{euro} €</b>  ·  ¥{yen}"
        # Taux indisponible : on n'invente pas une conversion. Le yen seul
        # vaut mieux qu'un euro faux.
        return f"<b>¥{yen}</b>"

    @staticmethod
    def _link(listing: Listing) -> str:
        """Le lien vers l'annonce, sur jp.mercari.com."""
        url = listing.buy_url or listing.url
        if not url:
            return ""
        label = "Voir sur Mercari" if "mercari.com" in url else "Ouvrir l'annonce"
        return f'<a href="{html.escape(url, quote=True)}">🔗 {label}</a>'

    def _clean(self, listing: Listing) -> str:
        """Trois lignes : le nom, le prix en euros, le lien. Rien d'autre.

        Un canal Telegram se lit sur un téléphone, souvent en marchant. Le
        mot-clé, la latence et le détail du score ont leur place dans le
        dashboard, pas ici : ils repoussent le lien hors de l'écran.
        """
        title = html.escape(listing.title[:150])
        source = html.escape(self.source_label(listing.source))

        lines = [f"<b>{title}</b>", "", self._prices(listing)]

        line = source
        if listing.tier and listing.tier != "NORMAL":
            line = f"{source}  ·  {html.escape(listing.tier)}"
        lines.append(f"<i>{line}</i>")

        link = self._link(listing)
        if link:
            lines += ["", link]
        return "\n".join(lines)

    def _detailed(self, listing: Listing) -> str:
        title = html.escape(listing.title[:180])
        keyword = html.escape(listing.keyword or ", ".join(listing.keywords[:2]))
        source = html.escape(self.source_label(listing.source))

        lines = [
            "🔥 <b>NOUVELLE ANNONCE</b>",
            "",
            f"<b>{title}</b>",
            "",
            self._prices(listing),
            f"🇯🇵 Source : {source}",
        ]
        if keyword:
            lines.append(f"🔎 Mot-clé : {keyword}")
        if listing.score:
            lines.append(f"⭐ Score : {listing.score}/100 · {listing.tier}")

        # La latence de détection n'est affichée que si elle est RÉELLEMENT
        # connue. Beaucoup de sources ne datent pas la publication ; afficher
        # « détectée en 0,0 s » serait une fausse promesse.
        if listing.latency_ms:
            lines.append(f"⚡ Détectée {listing.latency_ms / 1000:.1f} s "
                         f"après publication")
        elif listing.network_ms:
            lines.append(f"⚡ Réponse source en {listing.network_ms} ms")

        link = self._link(listing)
        if link:
            lines += ["", link]
        origin = listing.metadata.get("origin_url", "")
        if origin and origin != (listing.buy_url or listing.url):
            lines.append(
                f'↗️ <a href="{html.escape(origin, quote=True)}">Page d\'origine</a>'
            )
        return "\n".join(lines)

    @staticmethod
    def source_label(source: str) -> str:
        """« JDirectItems Auction » plutôt que « jdirectitems_auction »."""
        try:
            from ..platforms.registry import get

            spec = get(source)
            if spec is not None:
                return spec.label
        except Exception:      # noqa: BLE001 — jamais fatal pour un libellé
            pass
        return source.replace("sim_", "").replace("_", " ").title()

    # ── Envoi ─────────────────────────────────────────────────────────────
    async def send(self, listing: Listing) -> None:
        text = self.format(listing)

        if self.send_photo and listing.image_url:
            try:
                await self._call("sendPhoto", self._target({
                    "chat_id": self.chat_id,
                    "photo": listing.image_url,
                    "caption": text[:CAPTION_LIMIT],
                    "parse_mode": "HTML",
                    "disable_notification": self.silent,
                }))
                return
            except TelegramError as exc:
                if exc.retry_after is not None:
                    raise      # 429 : c'est au hub de temporiser
                # Vignette refusée (CDN bloqué, URL expirée) : on ne perd pas
                # l'annonce pour autant, on bascule en message texte.
                log.debug("photo refusée, repli texte : %s", exc)

        await self._call("sendMessage", self._target({
            "chat_id": self.chat_id,
            "text": text[:MESSAGE_LIMIT],
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
            "disable_notification": self.silent,
        }))

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
