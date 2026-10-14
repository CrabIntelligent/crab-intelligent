"""Append-only records and daily Merkle roots.

Every job created, review verdict, accepted step and delivery becomes a record. A record's leaf is
sha256(0x00 || canonical(body)); an inner node is sha256(0x01 || left || right). The prefixes keep a
leaf from ever being passed off as an inner node. An odd node at the end of a level is paired with
itself. Each UTC day's records form one tree; its root is what gets published on-chain.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .crypto import canonical, sha256_hex


def utc_day(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def leaf_hash(body: Dict[str, Any]) -> str:
    return hashlib.sha256(b"\x00" + canonical(body)).hexdigest()


def node_hash(left: str, right: str) -> str:
    return hashlib.sha256(b"\x01" + bytes.fromhex(left) + bytes.fromhex(right)).hexdigest()


def merkle_root(leaves: List[str]) -> Optional[str]:
    if not leaves:
        return None
    level = list(leaves)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [node_hash(level[i], level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


def merkle_proof(leaves: List[str], index: int) -> List[Dict[str, str]]:
    """Sibling hashes from leaf to root. `side` says where the sibling sits."""
    proof = []
    level = list(leaves)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        sibling = index ^ 1
        proof.append({"hash": level[sibling], "side": "left" if sibling < index else "right"})
        level = [node_hash(level[i], level[i + 1]) for i in range(0, len(level), 2)]
        index //= 2
    return proof


def verify_proof(leaf: str, proof: List[Dict[str, str]], root: str) -> bool:
    acc = leaf
    for step in proof:
        acc = node_hash(step["hash"], acc) if step["side"] == "left" else node_hash(acc, step["hash"])
    return acc == root


def add(conn: sqlite3.Connection, kind: str, job_id: Optional[str], step_id: Optional[str],
        actor: Optional[str], payload: Dict[str, Any]) -> int:
    ts = time.time()
    body = {
        "kind": kind,
        "job": job_id,
        "step": step_id,
        "actor": actor,
        "ts": round(ts, 3),
        "payload_sha256": sha256_hex(canonical(payload)),
    }
    cur = conn.execute(
        "INSERT INTO records (ts, day, kind, job_id, step_id, actor, payload, body, leaf) VALUES (?,?,?,?,?,?,?,?,?)",
        (ts, utc_day(ts), kind, job_id, step_id, actor, json.dumps(payload), json.dumps(body), leaf_hash(body)),
    )
    return int(cur.lastrowid)


def day_leaves(conn: sqlite3.Connection, day: str) -> List[sqlite3.Row]:
    return conn.execute("SELECT id, leaf FROM records WHERE day=? ORDER BY id", (day,)).fetchall()


def seal_past_days(conn: sqlite3.Connection) -> None:
    """Compute and store the root of every finished UTC day that has records but no root yet."""
    today = utc_day(time.time())
    days = conn.execute(
        "SELECT DISTINCT day FROM records WHERE day < ? AND day NOT IN (SELECT day FROM roots) ORDER BY day", (today,)
    ).fetchall()
    for row in days:
        leaves = [r["leaf"] for r in day_leaves(conn, row["day"])]
        conn.execute(
            "INSERT INTO roots (day, root, count, computed, tx) VALUES (?,?,?,?,NULL)",
            (row["day"], merkle_root(leaves), len(leaves), time.time()),
        )


def proof_for(conn: sqlite3.Connection, record_id: int) -> Optional[Dict[str, Any]]:
    rec = conn.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
    if not rec:
        return None
    rows = day_leaves(conn, rec["day"])
    leaves = [r["leaf"] for r in rows]
    index = [r["id"] for r in rows].index(record_id)
    sealed = conn.execute("SELECT root, tx FROM roots WHERE day=?", (rec["day"],)).fetchone()
    return {
        "record": {"id": rec["id"], "day": rec["day"], "body": json.loads(rec["body"]), "leaf": rec["leaf"]},
        "index": index,
        "count": len(leaves),
        "proof": merkle_proof(leaves, index),
        "root": sealed["root"] if sealed else merkle_root(leaves),
        "sealed": bool(sealed),
        "tx": sealed["tx"] if sealed else None,
    }
