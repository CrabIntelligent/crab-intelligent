import json

import base58
import pytest
from fastapi.testclient import TestClient
from nacl.signing import SigningKey

from server.main import app
from server.records import verify_proof
from worker.client import Client, Keypair
from worker.providers import ProviderError
from worker.providers.mock import MockProvider
from worker.runner import run

QUIET = lambda _msg: None  # noqa: E731


@pytest.fixture(scope="module")
def c():
    with TestClient(app) as client:
        yield client


def customer(c):
    token = c.post("/api/auth/dev", json={"role": "customer"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def order(c, headers, job_type, brief, public=True):
    resp = c.post("/api/jobs", json={"type": job_type, "brief": brief, "public": public}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def drain(c, tmp_path, provider_a=None, provider_b=None):
    """Two burrows on different model families take turns until no task is left."""
    a = Client("", Keypair(tmp_path / "a.json"), http=c)
    b = Client("", Keypair(tmp_path / "b.json"), http=c)
    pa = [provider_a or MockProvider("mock-a")]
    pb = [provider_b or MockProvider("mock-b")]
    for _ in range(40):
        handled = run(a, "a", pa, ["work", "review"], once=True, log=QUIET)
        handled += run(b, "b", pb, ["work", "review"], once=True, log=QUIET)
        if handled == 0:
            return


def test_report_job_runs_end_to_end_with_cross_family_reviews(c, tmp_path):
    h = customer(c)
    job = order(c, h, "report", "How do Solana validators earn rewards?")
    assert job["status"] == "running" and [s["key"] for s in job["steps"]] == ["outline", "report"]

    drain(c, tmp_path)
    job = c.get(f"/api/jobs/{job['id']}", headers=h).json()
    assert job["status"] == "completed"
    assert all(s["status"] == "accepted" for s in job["steps"])
    for step in job["steps"]:
        assert step["reviews"] and step["reviews"][-1]["independent"]
        assert step["reviews"][-1]["family"] != step["author_family"]

    report = job["deliverable"]["files"][0]
    resp = c.get(report["url"])
    assert resp.status_code == 200 and "Solana validators" in resp.text
    assert resp.headers["content-security-policy"].startswith("sandbox")

    record_id = job["steps"][0]["reviews"][-1]["record"]
    proof = c.get(f"/api/records/{record_id}/proof").json()
    assert verify_proof(proof["record"]["leaf"], proof["proof"], proof["root"])

    stats = c.get("/api/stats").json()
    assert stats["reviews_total"] >= 2 and stats["jobs_completed"] >= 1 and stats["burrows"] >= 2
    assert any(item["id"] == job["id"] for item in c.get("/api/feed").json())


def test_rework_goes_back_to_the_author_and_then_passes(c, tmp_path):
    h = customer(c)
    job = order(c, h, "report", "Compare proof of stake and proof of work.")
    drain(c, tmp_path, provider_b=MockProvider("mock-b", rework_rate=1.0))
    job = c.get(f"/api/jobs/{job['id']}", headers=h).json()
    assert job["status"] == "completed"
    verdicts = [r["verdict"] for s in job["steps"] for r in s["reviews"]]
    assert "rework" in verdicts and verdicts[-1] == "accepted"
    assert any(s["attempt"] == 2 for s in job["steps"])


def test_oracle_answer_is_signed_and_verifiable(c, tmp_path):
    h = customer(c)
    job = order(c, h, "oracle", "What is the capital of the United States?")
    drain(c, tmp_path)
    job = c.get(f"/api/jobs/{job['id']}", headers=h).json()
    assert job["status"] == "completed"
    signed = c.get(job["deliverable"]["files"][0]["url"]).json()
    check = c.post("/api/oracle/verify", json={"payload": signed["payload"], "signature": signed["signature"]}).json()
    assert check["valid"] is True
    tampered = dict(signed["payload"], answer="Something else")
    assert c.post("/api/oracle/verify", json={"payload": tampered, "signature": signed["signature"]}).json()["valid"] is False


class SloppyBuilder(MockProvider):
    """Returns prose instead of HTML on the first build attempt."""

    def work(self, task):
        if task["step"]["key"] == "build" and task["attempt"] == 1:
            return "Here is your website: a nice page.", self.model
        return super().work(task)


def test_format_gate_sends_broken_html_back_without_a_review(c, tmp_path):
    h = customer(c)
    job = order(c, h, "website", "A landing page for a bakery in Bandung.")
    drain(c, tmp_path, provider_a=SloppyBuilder("mock-a"), provider_b=SloppyBuilder("mock-b"))
    job = c.get(f"/api/jobs/{job['id']}", headers=h).json()
    assert job["status"] == "completed"
    build = job["steps"][1]
    assert build["attempt"] == 2
    assert [r["verdict"] for r in build["reviews"]] == ["accepted"]
    page = c.get(job["deliverable"]["files"][0]["url"])
    assert "<html" in page.text and "bakery" in page.text


class Declines(MockProvider):
    def work(self, task):
        raise ProviderError("declined")


def test_job_blocks_after_workers_give_a_step_back_three_times(c, tmp_path):
    h = customer(c)
    job = order(c, h, "report", "Anything.")
    drain(c, tmp_path, provider_a=Declines("mock-a"), provider_b=Declines("mock-b"))
    job = c.get(f"/api/jobs/{job['id']}", headers=h).json()
    assert job["status"] == "blocked" and "could not complete" in job["error"]


def test_wallet_sign_in_checks_the_signature_and_burns_the_nonce(c):
    key = SigningKey.generate()
    pubkey = base58.b58encode(bytes(key.verify_key)).decode()
    challenge = c.post("/api/auth/nonce", json={"pubkey": pubkey, "role": "customer"}).json()
    other = SigningKey.generate().sign(challenge["message"].encode()).signature.hex()
    bad = c.post("/api/auth/verify", json={"pubkey": pubkey, "nonce": challenge["nonce"], "signature": other})
    assert bad.status_code == 401

    challenge = c.post("/api/auth/nonce", json={"pubkey": pubkey, "role": "customer"}).json()
    sig = base58.b58encode(key.sign(challenge["message"].encode()).signature).decode()
    body = {"pubkey": pubkey, "nonce": challenge["nonce"], "signature": sig}
    session = c.post("/api/auth/verify", json=body).json()
    assert session["pubkey"] == pubkey and session["role"] == "customer"
    assert c.post("/api/auth/verify", json=body).status_code == 401
    me = c.get("/api/me", headers={"Authorization": f"Bearer {session['token']}"}).json()
    assert me["pubkey"] == pubkey

    assert c.post("/api/auth/nonce", json={"pubkey": "not-a-wallet"}).status_code == 400


def test_permissions_and_limits(c):
    alice, bob = customer(c), customer(c)
    private = order(c, alice, "report", "Private research.", public=False)
    assert c.get(f"/api/jobs/{private['id']}", headers=bob).status_code == 404
    assert c.get(f"/api/jobs/{private['id']}", headers=alice).status_code == 200
    assert c.get(f"/api/jobs/{private['id']}").status_code == 404

    public = order(c, alice, "report", "Public research.")
    seen = c.get(f"/api/jobs/{public['id']}", headers=bob).json()
    assert seen["deliverable"] is None and all(s["output"] is None for s in seen["steps"])

    assert c.post("/api/worker/claim", json={"families": ["mock-a"]}, headers=alice).status_code == 403
    assert c.post("/api/jobs", json={"type": "report", "brief": "  "}, headers=alice).status_code == 400
    assert c.post("/api/jobs", json={"type": "video", "brief": "A clip"}, headers=alice).status_code == 400
    assert c.post("/api/jobs", json={"type": "report", "brief": "x"}).status_code == 401
