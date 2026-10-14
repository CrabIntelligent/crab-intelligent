"""HTTP API and static pages. Run with:  .venv/bin/uvicorn server.main:app --reload"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import auth, config, db, delivery, engine, planner, records
from .crypto import canonical, parse_signature, verify
from .db import read, tx
from .errors import ApiError


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    delivery.oracle_signer()
    yield


app = FastAPI(title="Crab Intelligent", version="0.1.0", lifespan=lifespan)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://crbi.io", "https://www.crbi.io"],
    allow_methods=["GET"],
    allow_headers=[],
)


@app.exception_handler(ApiError)
async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse({"error": exc.message}, status_code=exc.status)


def _session(request: Request) -> Optional[Dict[str, Any]]:
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    return auth.session_for(token) if token else None


def any_session(request: Request) -> Dict[str, Any]:
    session = _session(request)
    if not session:
        raise ApiError("Sign in first.", 401)
    return session


def customer(session: Dict[str, Any] = Depends(any_session)) -> Dict[str, Any]:
    if session["role"] != "customer":
        raise ApiError("Sign in with a customer session to order jobs.", 403)
    return session


def worker(session: Dict[str, Any] = Depends(any_session)) -> Dict[str, Any]:
    if session["role"] != "worker":
        raise ApiError("This endpoint is for burrow workers.", 403)
    return session


# ---------------------------------------------------------------- request bodies

class NonceIn(BaseModel):
    pubkey: str
    role: str = "customer"


class VerifyIn(BaseModel):
    pubkey: str
    nonce: str
    signature: str


class DevIn(BaseModel):
    role: str = "customer"


class JobIn(BaseModel):
    type: str
    brief: str
    public: bool = True


class HeartbeatIn(BaseModel):
    name: Optional[str] = None
    families: List[str]
    models: Dict[str, str] = Field(default_factory=dict)


class ClaimIn(BaseModel):
    families: List[str]
    kinds: List[str] = Field(default_factory=lambda: ["work", "review"])


class SubmitIn(BaseModel):
    task_id: str
    output: str
    family: str
    model: Optional[str] = None


class ReviewIn(BaseModel):
    task_id: str
    verdict: str
    summary: str = ""
    findings: List[str] = Field(default_factory=list)
    family: str
    model: Optional[str] = None


class ReleaseIn(BaseModel):
    task_id: str
    reason: str = ""


class OracleVerifyIn(BaseModel):
    payload: Dict[str, Any]
    signature: str
    signer: Optional[str] = None


# ---------------------------------------------------------------- public

@app.get("/api/config")
def get_config() -> Dict[str, Any]:
    return {
        "dev_mode": config.DEV_MODE,
        "paused": config.PAUSED,
        "strict_cross_family": config.STRICT_CROSS_FAMILY,
        "max_brief_chars": config.MAX_BRIEF_CHARS,
        "job_types": planner.catalogue(),
        "oracle_signer": delivery.oracle_signer().pubkey,
        "ipfs": bool(config.PINATA_JWT),
    }


@app.get("/api/stats")
def get_stats() -> Dict[str, Any]:
    return engine.stats()


@app.get("/api/feed")
def get_feed() -> List[Dict[str, Any]]:
    return engine.feed()


@app.get("/api/roots")
def get_roots() -> Dict[str, Any]:
    with tx() as conn:
        records.seal_past_days(conn)
    with read() as conn:
        sealed = [dict(r) for r in conn.execute("SELECT day, root, count, tx FROM roots ORDER BY day DESC LIMIT 60")]
        today = conn.execute("SELECT day FROM records ORDER BY id DESC LIMIT 1").fetchone()
        open_day = None
        if today and not any(r["day"] == today["day"] for r in sealed):
            leaves = [r["leaf"] for r in records.day_leaves(conn, today["day"])]
            open_day = {"day": today["day"], "root": records.merkle_root(leaves), "count": len(leaves)}
    return {"sealed": sealed, "open": open_day}


@app.get("/api/records/{record_id}/proof")
def get_proof(record_id: int) -> Dict[str, Any]:
    with read() as conn:
        proof = records.proof_for(conn, record_id)
    if not proof:
        raise ApiError("Record not found.", 404)
    return proof


@app.post("/api/oracle/verify")
def oracle_verify(body: OracleVerifyIn) -> Dict[str, Any]:
    signer = body.signer or delivery.oracle_signer().pubkey
    sig = parse_signature(body.signature)
    ok = bool(sig) and verify(signer, canonical(body.payload), sig)
    return {"valid": ok, "signer": signer}


# ---------------------------------------------------------------- sign-in

@app.post("/api/auth/nonce")
def auth_nonce(body: NonceIn) -> Dict[str, str]:
    return auth.new_nonce(body.pubkey, body.role)


@app.post("/api/auth/verify")
def auth_verify(body: VerifyIn) -> Dict[str, Any]:
    return auth.verify_login(body.pubkey, body.nonce, body.signature)


@app.post("/api/auth/dev")
def auth_dev(body: DevIn) -> Dict[str, Any]:
    return auth.dev_login(body.role)


@app.get("/api/me")
def me(session: Dict[str, Any] = Depends(any_session)) -> Dict[str, Any]:
    return session


# ---------------------------------------------------------------- customers

@app.post("/api/jobs")
def create_job(body: JobIn, session: Dict[str, Any] = Depends(customer)) -> Dict[str, Any]:
    if config.PAUSED:
        raise ApiError("New orders are paused for the $CRBI launch. They reopen soon.", 503)
    return engine.create_job(session["pubkey"], body.type, body.brief, body.public)


@app.get("/api/jobs")
def my_jobs(session: Dict[str, Any] = Depends(customer)) -> List[Dict[str, Any]]:
    return engine.list_jobs(session["pubkey"])


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str, request: Request) -> Dict[str, Any]:
    session = _session(request)
    return engine.get_job(job_id, session["pubkey"] if session else None)


# ---------------------------------------------------------------- workers

@app.post("/api/worker/heartbeat")
def worker_heartbeat(body: HeartbeatIn, session: Dict[str, Any] = Depends(worker)) -> Dict[str, Any]:
    engine.heartbeat(session["pubkey"], body.name, body.families, body.models)
    return {"ok": True}


@app.post("/api/worker/claim")
def worker_claim(body: ClaimIn, session: Dict[str, Any] = Depends(worker)) -> Dict[str, Any]:
    return {"task": engine.claim(session["pubkey"], body.families, body.kinds)}


@app.post("/api/worker/submit")
def worker_submit(body: SubmitIn, session: Dict[str, Any] = Depends(worker)) -> Dict[str, Any]:
    return engine.submit_work(session["pubkey"], body.task_id, body.output, body.family, body.model)


@app.post("/api/worker/review")
def worker_review(body: ReviewIn, session: Dict[str, Any] = Depends(worker)) -> Dict[str, Any]:
    return engine.submit_review(
        session["pubkey"], body.task_id, body.verdict, body.summary, body.findings, body.family, body.model
    )


@app.post("/api/worker/release")
def worker_release(body: ReleaseIn, session: Dict[str, Any] = Depends(worker)) -> Dict[str, Any]:
    return engine.release(session["pubkey"], body.task_id, body.reason)


# ---------------------------------------------------------------- pages and delivered files

_SANDBOX = {
    # Delivered files are customer content. Serving HTML in a sandbox without allow-same-origin gives it
    # an opaque origin, so a delivered page can never read this site's storage or call the API as the user.
    "Content-Security-Policy": "sandbox allow-scripts allow-forms allow-popups allow-modals",
    "X-Content-Type-Options": "nosniff",
}
_TYPES = {".html": "text/html; charset=utf-8", ".md": "text/plain; charset=utf-8", ".json": "application/json"}


@app.get("/files/{job_id}/{name}", include_in_schema=False)
def delivered_file(job_id: str, name: str) -> FileResponse:
    folder = (config.FILES_DIR / job_id).resolve()
    path = (folder / name).resolve()
    if folder.parent != config.FILES_DIR.resolve() or path.parent != folder or not path.is_file():
        raise ApiError("File not found.", 404)
    return FileResponse(path, media_type=_TYPES.get(path.suffix, "application/octet-stream"), headers=_SANDBOX)


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse(config.WEB_DIR / "index.html", media_type="text/html; charset=utf-8")


@app.get("/app", include_in_schema=False)
def order_app() -> FileResponse:
    return FileResponse(config.WEB_DIR / "app.html", media_type="text/html; charset=utf-8")
