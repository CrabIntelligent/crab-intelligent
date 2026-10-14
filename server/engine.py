"""The job state machine.

A job is a list of steps run in order. Each step is written by one burrow (a "work" task) and then
checked by another burrow on a different model family (a "review" task). An accepted step unlocks the
next one; a rejected step goes back with the reviewer's findings, up to MAX_ATTEMPTS. When the last
step is accepted the job is delivered. Every decision is written to the records table.
"""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
import time
from typing import Any, Dict, List, Optional

from . import config, delivery, planner, records
from .crypto import sha256_hex
from .db import read, tx
from .errors import ApiError

FAMILY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
MAX_OUTPUT_CHARS = 400_000


def _id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def _check_families(families: List[str]) -> List[str]:
    if not families or not all(isinstance(f, str) and FAMILY_RE.match(f) for f in families):
        raise ApiError("families must be a non-empty list of short lowercase names, e.g. ['anthropic'].")
    return families


# ---------------------------------------------------------------- jobs

def create_job(owner: str, job_type: str, brief: str, public: bool) -> Dict[str, Any]:
    brief = (brief or "").strip()
    if not brief:
        raise ApiError("Describe the job in a few lines.")
    if len(brief) > config.MAX_BRIEF_CHARS:
        raise ApiError(f"Keep the brief under {config.MAX_BRIEF_CHARS} characters.")
    try:
        steps = planner.plan(job_type)
    except ValueError as exc:
        raise ApiError(str(exc))
    now = time.time()
    job_id = _id("job")
    with tx() as conn:
        running = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE owner=? AND status IN ('running','delivering')", (owner,)
        ).fetchone()[0]
        if running >= config.MAX_OPEN_JOBS_PER_WALLET:
            raise ApiError(f"You already have {running} jobs running. Wait for one to finish.", 429)
        conn.execute(
            "INSERT INTO jobs (id, owner, type, brief, public, status, created, updated, quote) VALUES (?,?,?,?,?,?,?,?,?)",
            (job_id, owner, job_type, brief, int(bool(public)), "running", now, now, json.dumps(planner.quote(job_type))),
        )
        for i, step in enumerate(steps):
            conn.execute(
                "INSERT INTO steps (id, job_id, idx, key, title, instruction, web, status, updated) VALUES (?,?,?,?,?,?,?,?,?)",
                (_id("step"), job_id, i, step["key"], step["title"], step["instruction"], int(step["web"]), "pending", now),
            )
        records.add(conn, "job.created", job_id, None, owner, {
            "type": job_type, "brief_sha256": sha256_hex(brief.encode()), "steps": [s["key"] for s in steps],
        })
        first = conn.execute("SELECT * FROM steps WHERE job_id=? ORDER BY idx LIMIT 1", (job_id,)).fetchone()
        _open_work(conn, first)
    return get_job(job_id, owner)


def _open_work(conn: sqlite3.Connection, step: sqlite3.Row) -> None:
    now = time.time()
    conn.execute("UPDATE steps SET status='open', updated=? WHERE id=?", (now, step["id"]))
    conn.execute(
        "INSERT INTO tasks (id, kind, step_id, job_id, status, created) VALUES (?, 'work', ?, ?, 'open', ?)",
        (_id("task"), step["id"], step["job_id"], now),
    )


def get_job(job_id: str, viewer: Optional[str]) -> Dict[str, Any]:
    with read() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job or (job["owner"] != viewer and not job["public"]):
            raise ApiError("Job not found.", 404)
        owner = job["owner"] == viewer
        steps = conn.execute("SELECT * FROM steps WHERE job_id=? ORDER BY idx", (job_id,)).fetchall()
        reviews = conn.execute("SELECT * FROM reviews WHERE job_id=? ORDER BY created", (job_id,)).fetchall()
    by_step: Dict[str, List[Dict[str, Any]]] = {}
    for r in reviews:
        by_step.setdefault(r["step_id"], []).append({
            "attempt": r["attempt"], "verdict": r["verdict"], "summary": r["summary"],
            "findings": json.loads(r["findings"] or "[]"), "family": r["family"], "model": r["model"],
            "independent": bool(r["independent"]), "record": r["record_id"], "at": r["created"],
        })
    return {
        "id": job["id"],
        "type": job["type"],
        "brief": job["brief"],
        "public": bool(job["public"]),
        "status": job["status"],
        "created": job["created"],
        "updated": job["updated"],
        "quote": json.loads(job["quote"] or "null"),
        "error": job["error"],
        "deliverable": json.loads(job["deliverable"]) if job["deliverable"] and owner else None,
        "owner": owner,
        "steps": [{
            "key": s["key"], "title": s["title"], "status": s["status"], "attempt": s["attempt"],
            "author_family": s["author_family"], "author_model": s["author_model"],
            "output": s["output"] if owner else None,
            "reviews": by_step.get(s["id"], []),
        } for s in steps],
    }


def list_jobs(owner: str) -> List[Dict[str, Any]]:
    with read() as conn:
        rows = conn.execute(
            "SELECT id FROM jobs WHERE owner=? ORDER BY created DESC LIMIT 50", (owner,)
        ).fetchall()
    return [get_job(r["id"], owner) for r in rows]


def feed(limit: int = 5) -> List[Dict[str, Any]]:
    with read() as conn:
        rows = conn.execute(
            "SELECT id, type, status, brief, created FROM jobs WHERE public=1 ORDER BY created DESC LIMIT ?", (limit,)
        ).fetchall()
    return [{
        "id": r["id"], "type": r["type"], "status": r["status"], "created": r["created"],
        "brief": r["brief"] if len(r["brief"]) <= 140 else r["brief"][:139].rstrip() + "…",
    } for r in rows]


# ---------------------------------------------------------------- workers

def heartbeat(worker: str, name: Optional[str], families: List[str], models: Dict[str, str]) -> None:
    _check_families(families)
    now = time.time()
    with tx() as conn:
        conn.execute(
            "INSERT INTO workers (pubkey, name, families, models, first_seen, last_seen) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(pubkey) DO UPDATE SET name=excluded.name, families=excluded.families, "
            "models=excluded.models, last_seen=excluded.last_seen",
            (worker, (name or "")[:64], json.dumps(families), json.dumps(models), now, now),
        )


def _expire_claims(conn: sqlite3.Connection, now: float) -> None:
    conn.execute(
        "UPDATE tasks SET status='open', claimed_by=NULL, claimed_family=NULL, claimed_at=NULL "
        "WHERE status='claimed' AND claimed_at < ?",
        (now - config.CLAIM_TTL_SECONDS,),
    )


def claim(worker: str, families: List[str], kinds: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """Hand the oldest task this worker may take. Reviews need a different family and a different
    worker than the author; a non-strict network relaxes that after REVIEW_FALLBACK_SECONDS."""
    _check_families(families)
    kinds = kinds or ["work", "review"]
    now = time.time()
    with tx() as conn:
        _expire_claims(conn, now)
        chosen, use_family = None, None
        for task in conn.execute("SELECT * FROM tasks WHERE status='open' ORDER BY created LIMIT 200"):
            if task["kind"] not in kinds:
                continue
            if task["kind"] == "work":
                chosen, use_family = task, families[0]
                break
            other = [f for f in families if f != task["exclude_family"]]
            if other and worker != task["exclude_worker"]:
                chosen, use_family = task, other[0]
                break
            if not config.STRICT_CROSS_FAMILY and now - task["created"] >= config.REVIEW_FALLBACK_SECONDS:
                chosen, use_family = task, (other or families)[0]
                break
        if chosen is None:
            return None
        conn.execute(
            "UPDATE tasks SET status='claimed', claimed_by=?, claimed_family=?, claimed_at=? WHERE id=?",
            (worker, use_family, now, chosen["id"]),
        )
        return _task_payload(conn, chosen, use_family)


def _task_payload(conn: sqlite3.Connection, task: sqlite3.Row, use_family: str) -> Dict[str, Any]:
    step = conn.execute("SELECT * FROM steps WHERE id=?", (task["step_id"],)).fetchone()
    job = conn.execute("SELECT * FROM jobs WHERE id=?", (task["job_id"],)).fetchone()
    all_steps = conn.execute("SELECT * FROM steps WHERE job_id=? ORDER BY idx", (job["id"],)).fetchall()
    previous = [
        {"key": s["key"], "title": s["title"], "output": s["output"]}
        for s in all_steps if s["idx"] < step["idx"] and s["status"] == "accepted"
    ]
    payload = {
        "task_id": task["id"],
        "kind": task["kind"],
        "use_family": use_family,
        "job": {"id": job["id"], "type": job["type"], "brief": job["brief"]},
        "step": {
            "key": step["key"], "title": step["title"], "instruction": step["instruction"],
            "web": bool(step["web"]), "index": step["idx"] + 1, "total": len(all_steps),
        },
        "attempt": step["attempt"],
        "previous": previous,
    }
    if task["kind"] == "work":
        payload["rework_notes"] = step["notes"] if step["attempt"] > 1 else None
        payload["previous_attempt"] = step["output"] if step["attempt"] > 1 else None
    else:
        payload["output"] = step["output"]
        payload["author_family"] = step["author_family"]
        payload["author_model"] = step["author_model"]
    return payload


def _claimed_task(conn: sqlite3.Connection, task_id: str, worker: str, kind: str) -> sqlite3.Row:
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not task or task["kind"] != kind:
        raise ApiError("Task not found.", 404)
    if task["status"] != "claimed" or task["claimed_by"] != worker:
        raise ApiError("This task is not claimed by you (it may have expired).", 409)
    return task


def _send_back(conn: sqlite3.Connection, job: sqlite3.Row, step: sqlite3.Row, notes: str) -> Dict[str, Any]:
    now = time.time()
    attempt = step["attempt"] + 1
    if attempt > config.MAX_ATTEMPTS:
        error = f"Step '{step['title']}' did not pass review after {config.MAX_ATTEMPTS} attempts."
        conn.execute("UPDATE steps SET status='failed', notes=?, updated=? WHERE id=?", (notes, now, step["id"]))
        conn.execute("UPDATE jobs SET status='blocked', error=?, updated=? WHERE id=?", (error, now, job["id"]))
        records.add(conn, "job.blocked", job["id"], step["id"], None, {"reason": error})
        return {"status": "blocked"}
    conn.execute("UPDATE steps SET attempt=?, notes=?, updated=? WHERE id=?", (attempt, notes, now, step["id"]))
    _open_work(conn, conn.execute("SELECT * FROM steps WHERE id=?", (step["id"],)).fetchone())
    return {"status": "rework", "attempt": attempt}


def submit_work(worker: str, task_id: str, output: str, family: str, model: Optional[str]) -> Dict[str, Any]:
    if len(output or "") > MAX_OUTPUT_CHARS:
        raise ApiError("Output is too large.", 413)
    now = time.time()
    with tx() as conn:
        task = _claimed_task(conn, task_id, worker, "work")
        if family != task["claimed_family"]:
            raise ApiError(f"This task was claimed for family '{task['claimed_family']}'.", 409)
        step = conn.execute("SELECT * FROM steps WHERE id=?", (task["step_id"],)).fetchone()
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (task["job_id"],)).fetchone()
        conn.execute("UPDATE tasks SET status='done', done_at=? WHERE id=?", (now, task_id))
        conn.execute(
            "UPDATE steps SET output=?, author=?, author_family=?, author_model=?, updated=? WHERE id=?",
            (output, worker, family, model, now, step["id"]),
        )
        ok, problem = planner.format_gate(job["type"], step["key"], output)
        if not ok:
            return _send_back(conn, job, step, f"Format check failed: {problem}")
        conn.execute("UPDATE steps SET status='review', updated=? WHERE id=?", (now, step["id"]))
        conn.execute(
            "INSERT INTO tasks (id, kind, step_id, job_id, status, exclude_family, exclude_worker, created) "
            "VALUES (?, 'review', ?, ?, 'open', ?, ?, ?)",
            (_id("task"), step["id"], job["id"], family, worker, now),
        )
    return {"status": "in_review"}


def submit_review(worker: str, task_id: str, verdict: str, summary: str, findings: List[str],
                  family: str, model: Optional[str]) -> Dict[str, Any]:
    if verdict not in ("accepted", "rework"):
        raise ApiError("verdict must be 'accepted' or 'rework'.")
    findings = [str(f)[:2000] for f in (findings or [])][:30]
    summary = (summary or "")[:4000]
    now = time.time()
    with tx() as conn:
        task = _claimed_task(conn, task_id, worker, "review")
        if family != task["claimed_family"]:
            raise ApiError(f"This task was claimed for family '{task['claimed_family']}'.", 409)
        step = conn.execute("SELECT * FROM steps WHERE id=?", (task["step_id"],)).fetchone()
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (task["job_id"],)).fetchone()
        independent = family != step["author_family"] and worker != step["author"]
        if config.STRICT_CROSS_FAMILY and not independent:
            raise ApiError("This network requires a reviewer on a different model family.", 409)
        conn.execute("UPDATE tasks SET status='done', done_at=? WHERE id=?", (now, task_id))
        record_id = records.add(conn, "review.verdict", job["id"], step["id"], worker, {
            "verdict": verdict, "attempt": step["attempt"], "family": family, "model": model,
            "independent": independent, "output_sha256": sha256_hex((step["output"] or "").encode()),
            "findings_sha256": sha256_hex(json.dumps([summary, findings]).encode()),
        })
        conn.execute(
            "INSERT INTO reviews (id, step_id, job_id, attempt, reviewer, family, model, verdict, summary, findings, "
            "independent, record_id, created) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_id("rev"), step["id"], job["id"], step["attempt"], worker, family, model, verdict, summary,
             json.dumps(findings), int(independent), record_id, now),
        )
        if verdict == "rework":
            notes = summary + ("\n" + "\n".join(f"- {f}" for f in findings) if findings else "")
            return _send_back(conn, job, step, notes)
        conn.execute("UPDATE steps SET status='accepted', updated=? WHERE id=?", (now, step["id"]))
        records.add(conn, "step.accepted", job["id"], step["id"], step["author"], {
            "key": step["key"], "attempt": step["attempt"], "family": step["author_family"],
            "model": step["author_model"], "output_sha256": sha256_hex((step["output"] or "").encode()),
            "review_record": record_id,
        })
        nxt = conn.execute("SELECT * FROM steps WHERE job_id=? AND idx=?", (job["id"], step["idx"] + 1)).fetchone()
        if nxt:
            _open_work(conn, nxt)
            return {"status": "accepted"}
        conn.execute("UPDATE jobs SET status='delivering', updated=? WHERE id=?", (now, job["id"]))
    deliver(job["id"])
    return {"status": "accepted", "delivered": True}


def release(worker: str, task_id: str, reason: str) -> Dict[str, Any]:
    """A worker gives a task back (its model declined, crashed, or timed out)."""
    now = time.time()
    with tx() as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task or task["status"] != "claimed" or task["claimed_by"] != worker:
            raise ApiError("This task is not claimed by you.", 409)
        step = conn.execute("SELECT * FROM steps WHERE id=?", (task["step_id"],)).fetchone()
        releases = step["releases"] + 1
        conn.execute("UPDATE steps SET releases=?, updated=? WHERE id=?", (releases, now, step["id"]))
        if releases >= config.MAX_RELEASES:
            error = f"Workers could not complete step '{step['title']}': {reason[:300]}"
            conn.execute("UPDATE tasks SET status='done', done_at=? WHERE id=?", (now, task_id))
            conn.execute("UPDATE steps SET status='failed', updated=? WHERE id=?", (now, step["id"]))
            conn.execute("UPDATE jobs SET status='blocked', error=?, updated=? WHERE id=?", (error, now, task["job_id"]))
            records.add(conn, "job.blocked", task["job_id"], step["id"], worker, {"reason": error})
            return {"status": "blocked"}
        conn.execute(
            "UPDATE tasks SET status='open', claimed_by=NULL, claimed_family=NULL, claimed_at=NULL WHERE id=?", (task_id,)
        )
    return {"status": "released"}


def deliver(job_id: str) -> None:
    with read() as conn:
        job = dict(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
        steps = [dict(s) for s in conn.execute("SELECT * FROM steps WHERE job_id=? ORDER BY idx", (job_id,))]
    now = time.time()
    try:
        result = delivery.build(job, steps)
    except (ValueError, OSError) as exc:
        with tx() as conn:
            conn.execute("UPDATE jobs SET status='blocked', error=?, updated=? WHERE id=?", (str(exc), now, job_id))
            records.add(conn, "job.blocked", job_id, None, None, {"reason": str(exc)})
        return
    with tx() as conn:
        conn.execute(
            "UPDATE jobs SET status='completed', deliverable=?, updated=? WHERE id=?", (json.dumps(result), now, job_id)
        )
        records.add(conn, "job.delivered", job_id, None, None, {
            "files": [{"name": f["name"], "sha256": f["sha256"]} for f in result["files"]],
            "ipfs_cid": (result.get("ipfs") or {}).get("cid"),
        })


# ---------------------------------------------------------------- network stats

def stats() -> Dict[str, Any]:
    now = time.time()
    with tx() as conn:
        records.seal_past_days(conn)
    with read() as conn:
        one = lambda sql, *args: conn.execute(sql, args).fetchone()[0]  # noqa: E731
        latest = conn.execute("SELECT day, root, count, tx FROM roots ORDER BY day DESC LIMIT 1").fetchone()
        log = conn.execute(
            "SELECT r.created, r.verdict, r.independent, j.type, s.key FROM reviews r "
            "JOIN steps s ON s.id=r.step_id JOIN jobs j ON j.id=r.job_id ORDER BY r.created DESC LIMIT 9"
        ).fetchall()
        return {
            "agents_online": one("SELECT COUNT(*) FROM workers WHERE last_seen > ?", now - config.ONLINE_WINDOW_SECONDS),
            "burrows": one("SELECT COUNT(*) FROM workers"),
            "working_now": one("SELECT COUNT(*) FROM tasks WHERE status='claimed'"),
            "open_tasks": one("SELECT COUNT(*) FROM tasks WHERE status='open'"),
            "jobs_total": one("SELECT COUNT(*) FROM jobs"),
            "jobs_completed": one("SELECT COUNT(*) FROM jobs WHERE status='completed'"),
            "accepted_24h": one("SELECT COUNT(*) FROM records WHERE kind='step.accepted' AND ts > ?", now - 86400),
            "reviews_total": one("SELECT COUNT(*) FROM reviews"),
            "reviews_accepted": one("SELECT COUNT(*) FROM reviews WHERE verdict='accepted'"),
            "reviews_rework": one("SELECT COUNT(*) FROM reviews WHERE verdict='rework'"),
            "records_total": one("SELECT COUNT(*) FROM records"),
            "roots_sealed": one("SELECT COUNT(*) FROM roots"),
            "roots_published": one("SELECT COUNT(*) FROM roots WHERE tx IS NOT NULL"),
            "latest_root": dict(latest) if latest else None,
            "log": [{
                "at": r["created"], "verdict": r["verdict"], "independent": bool(r["independent"]),
                "path": f"{r['type']}/{r['key']}",
            } for r in log],
        }
