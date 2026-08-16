"""Vérifie que les jetons DPoP sont des JWT ES256 valides."""

import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives import hashes
from cryptography.exceptions import InvalidSignature

from mercari_sniper.dpop import DPoPSigner

URL = "https://api.mercari.jp/v2/entities:search"


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _parts(token: str):
    header_b64, payload_b64, signature_b64 = token.split(".")
    return (
        json.loads(_b64url_decode(header_b64)),
        json.loads(_b64url_decode(payload_b64)),
        _b64url_decode(signature_b64),
        f"{header_b64}.{payload_b64}",
    )


class TestDPoPSigner:
    def test_token_has_three_parts(self):
        assert DPoPSigner().token("POST", URL).count(".") == 2

    def test_header_declares_es256_and_jwk(self):
        header, _, _, _ = _parts(DPoPSigner().token("POST", URL))
        assert header["typ"] == "dpop+jwt"
        assert header["alg"] == "ES256"
        assert header["jwk"]["kty"] == "EC"
        assert header["jwk"]["crv"] == "P-256"
        assert set(header["jwk"]) == {"crv", "kty", "x", "y"}

    def test_jwk_coordinates_are_32_bytes(self):
        header, _, _, _ = _parts(DPoPSigner().token("POST", URL))
        assert len(_b64url_decode(header["jwk"]["x"])) == 32
        assert len(_b64url_decode(header["jwk"]["y"])) == 32

    def test_payload_binds_method_and_url(self):
        _, payload, _, _ = _parts(DPoPSigner().token("POST", URL))
        assert payload["htm"] == "POST"
        assert payload["htu"] == URL
        assert payload["iat"] > 0
        assert payload["jti"]

    def test_method_is_uppercased(self):
        _, payload, _, _ = _parts(DPoPSigner().token("post", URL))
        assert payload["htm"] == "POST"

    def test_signature_is_raw_64_bytes_not_der(self):
        """ES256 exige R||S brut ; DER ferait rejeter le jeton par Mercari."""
        _, _, signature, _ = _parts(DPoPSigner().token("POST", URL))
        assert len(signature) == 64

    def test_signature_verifies_against_embedded_key(self):
        token = DPoPSigner().token("POST", URL)
        header, _, signature, signing_input = _parts(token)

        x = int.from_bytes(_b64url_decode(header["jwk"]["x"]), "big")
        y = int.from_bytes(_b64url_decode(header["jwk"]["y"]), "big")
        public_key = ec.EllipticCurvePublicNumbers(
            x, y, ec.SECP256R1()
        ).public_key()

        r = int.from_bytes(signature[:32], "big")
        s = int.from_bytes(signature[32:], "big")
        public_key.verify(
            encode_dss_signature(r, s),
            signing_input.encode(),
            ec.ECDSA(hashes.SHA256()),
        )  # ne lève pas => signature valide

    def test_tampered_payload_fails_verification(self):
        signer = DPoPSigner()
        header, _, signature, _ = _parts(signer.token("POST", URL))
        x = int.from_bytes(_b64url_decode(header["jwk"]["x"]), "big")
        y = int.from_bytes(_b64url_decode(header["jwk"]["y"]), "big")
        public_key = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()

        r = int.from_bytes(signature[:32], "big")
        s = int.from_bytes(signature[32:], "big")
        with pytest.raises(InvalidSignature):
            public_key.verify(
                encode_dss_signature(r, s),
                b"charge utile falsifiee",
                ec.ECDSA(hashes.SHA256()),
            )

    def test_jti_is_unique_per_token(self):
        signer = DPoPSigner()
        jtis = {_parts(signer.token("POST", URL))[1]["jti"] for _ in range(50)}
        assert len(jtis) == 50

    def test_uuid_stable_within_session(self):
        signer = DPoPSigner()
        first = _parts(signer.token("POST", URL))[1]["uuid"]
        second = _parts(signer.token("POST", URL))[1]["uuid"]
        assert first == second

    def test_rotate_changes_key(self):
        signer = DPoPSigner()
        before = _parts(signer.token("POST", URL))[0]["jwk"]["x"]
        signer.rotate()
        after = _parts(signer.token("POST", URL))[0]["jwk"]["x"]
        assert before != after
