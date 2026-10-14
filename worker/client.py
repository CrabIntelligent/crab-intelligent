"""Talks to the dispatcher. Signs in with the burrow's own ed25519 (Solana) keypair."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import base58
import httpx
from nacl.signing import SigningKey


class Keypair:
    """A burrow identity stored as {"seed": base58, "pubkey": base58}. Created on first run."""

    def __init__(self, path: Path):
        self.path = path
        if path.exists():
            self.key = SigningKey(base58.b58decode(json.loads(path.read_text())["seed"]))
        else:
            self.key = SigningKey.generate()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"seed": base58.b58encode(bytes(self.key)).decode(), "pubkey": self.pubkey}))
            os.chmod(path, 0o600)

    @property
    def pubkey(self) -> str:
        return base58.b58encode(bytes(self.key.verify_key)).decode()

    def sign(self, message: bytes) -> bytes:
        return self.key.sign(message).signature


class DispatcherError(Exception):
    pass


class Client:
    def __init__(self, server: str, keypair: Keypair, http: Optional[httpx.Client] = None):
        self.keypair = keypair
        self.http = http or httpx.Client(base_url=server.rstrip("/"), timeout=60)
        self.token: Optional[str] = None

    def login(self) -> None:
        challenge = self._post("/api/auth/nonce", {"pubkey": self.keypair.pubkey, "role": "worker"}, auth=False)
        signature = self.keypair.sign(challenge["message"].encode()).hex()
        session = self._post(
            "/api/auth/verify",
            {"pubkey": self.keypair.pubkey, "nonce": challenge["nonce"], "signature": signature},
            auth=False,
        )
        self.token = session["token"]

    def _post(self, path: str, body: Dict[str, Any], auth: bool = True, retry: bool = True) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.token}"} if auth and self.token else {}
        resp = self.http.post(path, json=body, headers=headers)
        if resp.status_code == 401 and auth and retry:
            self.login()
            return self._post(path, body, auth, retry=False)
        if resp.is_error:
            try:
                message = resp.json().get("error", resp.text)
            except ValueError:
                message = resp.text
            raise DispatcherError(f"{path}: {resp.status_code} {message}")
        return resp.json()

    def heartbeat(self, name: str, families: List[str], models: Dict[str, str]) -> None:
        self._post("/api/worker/heartbeat", {"name": name, "families": families, "models": models})

    def claim(self, families: List[str], kinds: List[str]) -> Optional[Dict[str, Any]]:
        return self._post("/api/worker/claim", {"families": families, "kinds": kinds})["task"]

    def submit(self, task_id: str, output: str, family: str, model: Optional[str]) -> Dict[str, Any]:
        return self._post("/api/worker/submit", {"task_id": task_id, "output": output, "family": family, "model": model})

    def review(self, task_id: str, verdict: str, summary: str, findings: List[str], family: str,
               model: Optional[str]) -> Dict[str, Any]:
        return self._post("/api/worker/review", {
            "task_id": task_id, "verdict": verdict, "summary": summary, "findings": findings,
            "family": family, "model": model,
        })

    def release(self, task_id: str, reason: str) -> Dict[str, Any]:
        return self._post("/api/worker/release", {"task_id": task_id, "reason": reason[:500]})
