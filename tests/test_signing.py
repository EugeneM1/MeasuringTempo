"""sign_request round-trips: sign with a generated key, verify with its public
half — exactly what Kalshi's server does — and reject tampering."""

import base64

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from tempo.signing import auth_headers, sign_request

TS, METHOD, PATH = 1791380000000, "GET", "/trade-api/ws/v2"
MESSAGE = f"{TS}{METHOD}{PATH}".encode()


def write_pem(tmp_path, key, name):
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    path = tmp_path / name
    path.write_bytes(pem)
    return str(path)


def test_ed25519_round_trip(tmp_path):
    key = ed25519.Ed25519PrivateKey.generate()
    sig_b64 = sign_request(write_pem(tmp_path, key, "ed.pem"), TS, METHOD, PATH)
    signature = base64.b64decode(sig_b64)
    assert len(signature) == 64 and len(sig_b64) == 88
    key.public_key().verify(signature, MESSAGE)  # raises on failure


def test_rsa_pss_round_trip(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    signature = base64.b64decode(sign_request(write_pem(tmp_path, key, "rsa.pem"), TS, METHOD, PATH))
    key.public_key().verify(
        signature,
        MESSAGE,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256().digest_size),
        hashes.SHA256(),
    )


def test_tampered_message_rejected(tmp_path):
    key = ed25519.Ed25519PrivateKey.generate()
    signature = base64.b64decode(sign_request(write_pem(tmp_path, key, "ed.pem"), TS, METHOD, PATH))
    with pytest.raises(Exception):
        key.public_key().verify(signature, MESSAGE + b"x")


def test_auth_headers_shape(tmp_path):
    key = ed25519.Ed25519PrivateKey.generate()
    headers = auth_headers("my-key-id", write_pem(tmp_path, key, "ed.pem"), TS, METHOD, PATH)
    assert headers["KALSHI-ACCESS-KEY"] == "my-key-id"
    assert headers["KALSHI-ACCESS-TIMESTAMP"] == str(TS)
    # the signature header verifies against the same message
    key.public_key().verify(base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"]), MESSAGE)
