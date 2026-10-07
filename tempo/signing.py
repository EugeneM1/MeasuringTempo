"""Kalshi request signing. Spec notes live in 03_ws_kalshi.py's docstring.

Signed string: str(timestamp_ms) + METHOD + path, UTF-8 bytes, no separators.
Ed25519 keys sign the bytes directly (RFC 8032); RSA keys use PSS/SHA-256 with
salt length = digest length. Output is standard base64 text.
"""

import base64
from functools import lru_cache
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa
from cryptography.hazmat.primitives.serialization import load_pem_private_key


@lru_cache(maxsize=4)
def _load_key(private_key_path: str):
    """Load and cache the private key; the file doesn't change mid-run."""
    return load_pem_private_key(Path(private_key_path).read_bytes(), password=None)


def sign_request(private_key_path: str, timestamp_ms: int, method: str, path: str) -> str:
    key = _load_key(private_key_path)
    message = (str(timestamp_ms) + method + path).encode("utf-8")
    if isinstance(key, ed25519.Ed25519PrivateKey):
        signature = key.sign(message)
    elif isinstance(key, rsa.RSAPrivateKey):
        signature = key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256().digest_size),
            hashes.SHA256(),
        )
    else:
        raise TypeError(f"unsupported key type {type(key).__name__} — Kalshi issues Ed25519 or RSA keys")
    return base64.b64encode(signature).decode()


def auth_headers(key_id: str, private_key_path: str, timestamp_ms: int, method: str, path: str) -> dict[str, str]:
    """The three signed Kalshi headers. One timestamp signs and ships."""
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-TIMESTAMP": str(timestamp_ms),
        "KALSHI-ACCESS-SIGNATURE": sign_request(private_key_path, timestamp_ms, method, path),
    }
