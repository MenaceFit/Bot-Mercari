"""Génération des jetons DPoP exigés par l'API Mercari.

Mercari signe chaque requête avec un JWT ES256 « DPoP » dont la clé publique
est embarquée dans l'en-tête (jwk). On génère une paire de clés éphémère par
session, et un jeton frais par requête (jti + iat uniques).

Implémenté directement sur `cryptography` : pas de dépendance JWT tierce,
et la signature ES256 doit être au format brut R||S (64 octets), pas DER.
"""

from __future__ import annotations

import base64
import json
import time
import uuid

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature


def _b64url(data: bytes) -> str:
    """base64url sans padding, comme l'exige JOSE."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _json_compact(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()


class DPoPSigner:
    """Signeur DPoP réutilisable.

    L'en-tête JWT (qui contient la clé publique) ne change jamais pour une
    clé donnée : on le sérialise une seule fois. Sur un scan à plusieurs
    requêtes/seconde, ça évite de re-sérialiser le JWK à chaque appel.
    """

    __slots__ = ("_key", "_header_b64", "_uuid")

    def __init__(self, key: ec.EllipticCurvePrivateKey | None = None) -> None:
        self._key = key or ec.generate_private_key(ec.SECP256R1())
        numbers = self._key.public_key().public_numbers()
        header = {
            "typ": "dpop+jwt",
            "alg": "ES256",
            "jwk": {
                "crv": "P-256",
                "kty": "EC",
                "x": _b64url(numbers.x.to_bytes(32, "big")),
                "y": _b64url(numbers.y.to_bytes(32, "big")),
            },
        }
        self._header_b64 = _b64url(_json_compact(header))
        # Identifiant d'appareil stable pour la durée de la session.
        self._uuid = str(uuid.uuid4())

    def rotate(self) -> None:
        """Régénère la paire de clés (nouvelle identité de session)."""
        self.__init__()  # type: ignore[misc]

    def token(self, method: str, url: str) -> str:
        payload = {
            "iat": int(time.time()),
            "jti": str(uuid.uuid4()),
            "htu": url,
            "htm": method.upper(),
            "uuid": self._uuid,
        }
        signing_input = f"{self._header_b64}.{_b64url(_json_compact(payload))}"

        der_signature = self._key.sign(
            signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256())
        )
        r, s = decode_dss_signature(der_signature)
        raw_signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")

        return f"{signing_input}.{_b64url(raw_signature)}"
