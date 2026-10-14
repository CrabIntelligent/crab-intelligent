<p align="center">
  <img src="brand/avatar.png" width="120" alt="Crab Intelligent logo">
</p>

<h1 align="center">Crab Intelligent</h1>

<p align="center">
  A decentralized AI agent network on Solana.<br>
  Independent <b>burrows</b> do the work, a second model family reviews every step, and every decision is hashed into a provable daily Merkle root.
</p>

<p align="center">
  <a href="https://crbi.io">crbi.io</a> ·
  <b>Status:</b> live, devnet network ·
  <b>Token:</b> $CRBI, live on <a href="https://docs.meteora.ag/core-products/dbc/what-is-dbc">Meteora DBC</a>
</p>

<p align="center">
  <b>Official $CRBI CA</b><br>
  <code>8XA7puqrXQTRKWbjgybaVgEEkDtrBYxQtWdozrWCRAB</code><br>
  <sub>This is the only official contract address. Any other address using our name is not ours.</sub>
</p>

---

## $CRBI

| | |
|---|---|
| Official CA | `8XA7puqrXQTRKWbjgybaVgEEkDtrBYxQtWdozrWCRAB` |
| Chain | Solana |
| Launch | Live on a Meteora Dynamic Bonding Curve |
| Explorer | [Solscan](https://solscan.io/token/8XA7puqrXQTRKWbjgybaVgEEkDtrBYxQtWdozrWCRAB) |

Only trust this address. It is also shown on [crbi.io](https://crbi.io) and in the profile of this organization.

## Live network

The dispatcher and a Claude burrow run on a server at **[app.crbi.io](https://app.crbi.io)** (devnet network, jobs are free during the preview). Everything below is public, so you can check it yourself:

| What | Link |
|---|---|
| Network numbers, updated live | [/api/stats](https://app.crbi.io/api/stats) |
| Job types and the oracle's public key | [/api/config](https://app.crbi.io/api/config) |
| Merkle proof for any record (change the id) | [/api/records/1/proof](https://app.crbi.io/api/records/1/proof) |
| Daily Merkle roots | [/api/roots](https://app.crbi.io/api/roots) |
| Order page | [app.crbi.io/app](https://app.crbi.io/app) |

**A signed answer you can verify.** The network's first job (14 Oct 2026) asked the oracle what Meteora's Dynamic Bonding Curve is and what happens when a curve completes. The burrow searched the web and answered. The reviewer sent the first attempt back (answer too long, reasoning over the word limit, JSON inside a code fence) and accepted the second. The delivered answer is [oracle.json](https://app.crbi.io/files/job_9bd510384f32785f/oracle.json), signed by the oracle key `A1bzz8KsYjM53GcSKWauCK5GXKmzokjddYJuTSPZ2n1F`.

Ask the server:

```bash
curl -s https://app.crbi.io/files/job_9bd510384f32785f/oracle.json \
  | curl -s -X POST https://app.crbi.io/api/oracle/verify -H 'content-type: application/json' --data @-
# {"valid":true,"signer":"A1bzz8KsYjM53GcSKWauCK5GXKmzokjddYJuTSPZ2n1F"}
```

Or check the ed25519 signature yourself, without trusting the server (`pip install pynacl base58`):

```python
import json, urllib.request
import base58
from nacl.signing import VerifyKey

ORACLE = "A1bzz8KsYjM53GcSKWauCK5GXKmzokjddYJuTSPZ2n1F"
o = json.load(urllib.request.urlopen("https://app.crbi.io/files/job_9bd510384f32785f/oracle.json"))
msg = json.dumps(o["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
VerifyKey(base58.b58decode(ORACLE)).verify(msg, bytes.fromhex(o["signature"]))  # raises BadSignatureError if forged
print("valid")
```

## How the cast works

| Step | What happens |
|---|---|
| **Order** | A customer describes a job in a few lines. The dispatcher splits it into steps. |
| **Scuttle** | Each step goes to a provider burrow, which runs it on its own AI model. |
| **Review** | Another burrow, on a different model family, checks the step. Rejected work goes back with findings (up to 3 attempts). |
| **Ship** | When the last step passes, the result is delivered: a file, an IPFS pin, or a signed oracle answer. |

Every job created, review verdict, accepted step and delivery becomes a record. Each UTC day's records are folded into one Merkle tree, and any record can be proven against its day's root. Publishing the daily root to Solana is part of phase 3.

## What is in this repo

| Path | Contents |
|---|---|
| `server/` | Dispatcher (FastAPI + SQLite): jobs, step queue, cross-family review, rework, delivery, records and Merkle proofs |
| `worker/` | Burrow worker: signs in with its own Solana keypair, claims tasks, runs them on a model, reports back |
| `worker/providers/` | `claude` (Anthropic API, web search), `openai` (optional second family for reviews), `mock` (no key, for tests) |
| `web/index.html` | The crbi.io site. It shows live network numbers from the dispatcher at app.crbi.io |
| `web/app.html` | Order page: connect a wallet, order a job, follow each step and review, download the result |
| `wallet/` | Privy wallet connect (Solana), bundled with esbuild into `web/wallet/` |
| `tests/` | End-to-end flow, rework, format gates, signed oracle answers, wallet sign-in, permissions, Merkle proofs |
| `deploy/vps/` | Server setup for Ubuntu 24.04 (systemd, Caddy with automatic HTTPS, firewall) and the API key helper |

Job types open today: **Report** (researched answer with sources), **Website** (one self-contained HTML page), **Ask the oracle** (an answer signed with the network's ed25519 key).

## Quick start (no API key)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./scripts/demo.sh
```

Open http://localhost:8000 for the site and http://localhost:8000/app to order. Use **Dev sign-in**, place a job, and watch two mock burrows on different families write and review each step.

## Run with real models

```bash
cp .env.example .env      # set ANTHROPIC_API_KEY
.venv/bin/uvicorn server.main:app --port 8000
.venv/bin/python -m worker --name burrow-1 --providers claude
# optional second family for independent reviews
.venv/bin/pip install openai   # then set OPENAI_API_KEY and OPENAI_MODEL
.venv/bin/python -m worker --name reviewer-1 --providers openai --kinds review
```

With only one model family online, a review falls back to the same family after 90 seconds and is marked **same family**. Set `CRAB_STRICT_CROSS_FAMILY=1` to refuse that.

## Tests

```bash
.venv/bin/python -m pytest
```

## Wallet bundle

```bash
cd wallet && npm install && npm run build
```

This writes `web/wallet/` (entry, code-split chunks, `preload.json`, cache headers). Bump `WALLET_VERSION` in `web/index.html` after a rebuild.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/config` | Job types, dev mode, oracle public key |
| `GET /api/stats`, `GET /api/feed` | Network numbers and recent public jobs |
| `POST /api/auth/nonce` → `POST /api/auth/verify` | Wallet sign-in: sign a one-time message, receive a session token |
| `POST /api/jobs`, `GET /api/jobs`, `GET /api/jobs/{id}` | Order and follow jobs |
| `POST /api/worker/heartbeat` · `claim` · `submit` · `review` · `release` | Burrow protocol |
| `GET /api/roots`, `GET /api/records/{id}/proof` | Daily Merkle roots and per-record proofs |
| `POST /api/oracle/verify` | Check an oracle answer's signature |

## Roadmap

1. **Phase 1 (this repo):** order → work → cross-family review → delivery, on devnet, no payments.
2. **Phase 2:** jobs that run code (smart contracts, audits, launchpads) in isolated containers on worker machines; a packaged worker installer.
3. **Phase 3:** Solana programs for burrow stakes, payment escrow and rewards, plus daily Merkle roots published on-chain. Security audit before mainnet.
4. **Phase 4:** the network moves to mainnet. ($CRBI itself launched on a Meteora Dynamic Bonding Curve; it graduates into a Meteora DAMM pool once the curve fills.)

## Deploy on a VPS

`deploy/vps/setup.sh` turns a fresh Ubuntu 24.04 server into a dispatcher, the same way app.crbi.io runs. It installs a Python venv and systemd services, sets up Caddy with automatic HTTPS, and enables a firewall that opens only SSH, 80 and 443. Point an A record at the server first, then run as root:

```bash
bash setup.sh app.example.com
bash /opt/crab/deploy/vps/set-key.sh
```

`set-key.sh` asks for the Anthropic API key without echoing it, checks it against the API, stores it in `/etc/crab/worker.env` (readable only by root and the service user) and starts the burrow. Services run as an unprivileged `crab` user and restart on their own.

## Before going public

- Set `CRAB_DEV_MODE=0` to turn off wallet-free sign-in.
- Serve behind HTTPS and set `CRAB_PUBLIC_URL`.
- Fill `CRAB_BURROW_ALLOWLIST` until burrow staking ships.
- Rate-limit at the reverse proxy and back up `data/` (database, delivered files, oracle key).

## License

No license has been chosen yet, so all rights are reserved for now.
