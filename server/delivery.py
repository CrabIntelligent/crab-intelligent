"""Turn a finished job into files: saved locally, pinned to IPFS when a Pinata key is set,
and for oracle answers, signed with the network's oracle key."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx

from . import config, planner
from .crypto import Signer, canonical, sha256_hex

_oracle: Optional[Signer] = None


def oracle_signer() -> Signer:
    global _oracle
    if _oracle is None:
        _oracle = Signer(config.ORACLE_KEY_PATH)
    return _oracle


def _save(job_id: str, name: str, data: bytes) -> Dict[str, Any]:
    folder = config.FILES_DIR / job_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)
    return {"name": name, "url": f"/files/{job_id}/{name}", "sha256": sha256_hex(data), "size": len(data)}


def _pin(job_id: str, name: str, data: bytes, mime: str) -> Dict[str, Any]:
    """Pin one file with Pinata's pinFileToIPFS endpoint."""
    try:
        resp = httpx.post(
            "https://api.pinata.cloud/pinning/pinFileToIPFS",
            headers={"Authorization": f"Bearer {config.PINATA_JWT}"},
            files={"file": (name, data, mime)},
            data={"pinataMetadata": json.dumps({"name": f"crab-{job_id}-{name}"})},
            timeout=60,
        )
        resp.raise_for_status()
        cid = resp.json()["IpfsHash"]
        return {"cid": cid, "url": f"https://ipfs.io/ipfs/{cid}"}
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        return {"error": f"IPFS pin failed: {exc}"}


def sign_oracle(payload: Dict[str, Any]) -> Dict[str, Any]:
    signer = oracle_signer()
    message = canonical(payload)
    return {
        "payload": payload,
        "signer": signer.pubkey,
        "algorithm": "ed25519",
        "encoding": "canonical-json (sorted keys, no whitespace, UTF-8)",
        "signature": signer.sign(message).hex(),
    }


def build(job: Dict[str, Any], steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Produce the deliverable for a job whose steps are all accepted."""
    final = steps[-1]["output"] or ""
    files = []
    ipfs = None
    if job["type"] == "report":
        data = final.encode()
        files.append(_save(job["id"], "report.md", data))
        main, mime = ("report.md", data), "text/markdown"
    elif job["type"] == "website":
        html = planner.extract_html(final) or final
        data = html.encode()
        files.append(_save(job["id"], "index.html", data))
        main, mime = ("index.html", data), "text/html"
    elif job["type"] == "oracle":
        answer = planner.extract_oracle(final)
        if answer is None:
            raise ValueError("Oracle output is not valid JSON")
        signed = sign_oracle({"job": job["id"], "question": job["brief"], **answer})
        data = json.dumps(signed, indent=2, ensure_ascii=False).encode()
        files.append(_save(job["id"], "oracle.json", data))
        main, mime = ("oracle.json", data), "application/json"
    else:
        raise ValueError(f"No delivery for job type {job['type']}")
    if config.PINATA_JWT:
        ipfs = _pin(job["id"], main[0], main[1], mime)
    return {"files": files, "ipfs": ipfs}
