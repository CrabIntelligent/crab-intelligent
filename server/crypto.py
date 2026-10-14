"""Ed25519 (Solana wallet keys), base58, hashing and canonical JSON."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional

import base58
from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey


def b58encode(data: bytes) -> str:
    return base58.b58encode(data).decode()


def b58decode(text: str) -> bytes:
    return base58.b58decode(text)


def is_pubkey(text: str) -> bool:
    try:
        return len(b58decode(text)) == 32
    except (ValueError, TypeError):
        return False


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(obj: Any) -> bytes:
    """Deterministic JSON: sorted keys, no whitespace, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def parse_signature(text: str) -> Optional[bytes]:
    """Accept a 64-byte signature as hex, base58 or base64."""
    text = text.strip()
    for decode in (bytes.fromhex, b58decode, base64.b64decode):
        try:
            sig = decode(text)
        except (ValueError, TypeError, binascii.Error):
            continue
        if len(sig) == 64:
            return sig
    return None


def verify(pubkey_b58: str, message: bytes, signature: bytes) -> bool:
    try:
        VerifyKey(b58decode(pubkey_b58)).verify(message, signature)
        return True
    except (BadSignatureError, ValueError, TypeError):
        return False


class Signer:
    """An ed25519 key kept in a JSON file (seed in base58). Created on first use."""

    def __init__(self, path: Path):
        self.path = path
        if path.exists():
            seed = b58decode(json.loads(path.read_text())["seed"])
            self.key = SigningKey(seed)
        else:
            self.key = SigningKey.generate()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"seed": b58encode(bytes(self.key)), "pubkey": self.pubkey}))
            os.chmod(path, 0o600)

    @property
    def pubkey(self) -> str:
        return b58encode(bytes(self.key.verify_key))

    def sign(self, message: bytes) -> bytes:
        return self.key.sign(message).signature
