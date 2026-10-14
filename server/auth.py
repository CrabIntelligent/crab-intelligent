"""Sign-in with a Solana wallet: the server issues a one-time message, the wallet signs it,
the server checks the ed25519 signature and hands back a session token."""
from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from . import config
from .crypto import b58encode, is_pubkey, parse_signature, verify
from .db import read, tx
from .errors import ApiError

ROLES = ("customer", "worker")
NONCE_TTL = 300


def _check_role(pubkey: str, role: str) -> None:
    if role not in ROLES:
        raise ApiError("Role must be 'customer' or 'worker'.")
    if role == "worker" and config.BURROW_ALLOWLIST and pubkey not in config.BURROW_ALLOWLIST:
        raise ApiError("This wallet has no burrow on this network.", 403)


def new_nonce(pubkey: str, role: str) -> Dict[str, str]:
    if not is_pubkey(pubkey):
        raise ApiError("That is not a Solana wallet address.")
    _check_role(pubkey, role)
    nonce = secrets.token_hex(16)
    issued = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    message = (
        "Crab Intelligent wants you to sign in.\n\n"
        f"Wallet: {pubkey}\nRole: {role}\nNonce: {nonce}\nIssued: {issued}\n\n"
        "Signing is free and does not send a transaction."
    )
    with tx() as conn:
        conn.execute("DELETE FROM nonces WHERE expires < ?", (time.time(),))
        conn.execute(
            "INSERT INTO nonces (nonce, pubkey, role, message, expires) VALUES (?,?,?,?,?)",
            (nonce, pubkey, role, message, time.time() + NONCE_TTL),
        )
    return {"nonce": nonce, "message": message}


def _issue(conn, pubkey: str, role: str) -> Dict[str, Any]:
    token = secrets.token_urlsafe(32)
    now = time.time()
    conn.execute(
        "INSERT INTO sessions (token, pubkey, role, created, expires) VALUES (?,?,?,?,?)",
        (token, pubkey, role, now, now + config.SESSION_TTL_SECONDS),
    )
    return {"token": token, "pubkey": pubkey, "role": role}


def verify_login(pubkey: str, nonce: str, signature: str) -> Dict[str, Any]:
    with tx() as conn:
        row = conn.execute("SELECT * FROM nonces WHERE nonce=?", (nonce,)).fetchone()
        if not row or row["pubkey"] != pubkey or row["expires"] < time.time():
            raise ApiError("This sign-in request expired. Start again.", 401)
        conn.execute("DELETE FROM nonces WHERE nonce=?", (nonce,))
        sig = parse_signature(signature)
        if sig is None or not verify(pubkey, row["message"].encode(), sig):
            raise ApiError("The signature does not match this wallet.", 401)
        return _issue(conn, pubkey, row["role"])


def dev_login(role: str) -> Dict[str, Any]:
    if not config.DEV_MODE:
        raise ApiError("Dev sign-in is turned off on this network.", 403)
    pubkey = b58encode(secrets.token_bytes(32))
    _check_role(pubkey, role)
    with tx() as conn:
        return _issue(conn, pubkey, role)


def session_for(token: str) -> Optional[Dict[str, Any]]:
    with read() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
    if not row or row["expires"] < time.time():
        return None
    return {"pubkey": row["pubkey"], "role": row["role"]}
