"""Settings, read once from the environment (and an optional .env file in the project root)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


DATA_DIR = Path(os.environ.get("CRAB_DATA_DIR") or ROOT / "data")
DB_PATH = DATA_DIR / "crab.db"
FILES_DIR = DATA_DIR / "files"
ORACLE_KEY_PATH = DATA_DIR / "oracle_key.json"
WEB_DIR = ROOT / "web"

# Dev mode allows one-click sign-in without a wallet. Turn it off before going public.
DEV_MODE = _bool("CRAB_DEV_MODE", True)
PUBLIC_URL = os.environ.get("CRAB_PUBLIC_URL", "http://localhost:8000").rstrip("/")

# Worker wallets allowed to open a burrow. Empty means any wallet may run a worker.
BURROW_ALLOWLIST = {s.strip() for s in os.environ.get("CRAB_BURROW_ALLOWLIST", "").split(",") if s.strip()}

# Reviews must come from a different model family than the author. When no such worker is
# online, a non-strict network lets a same-family worker review after the fallback delay
# (the review is marked same_family so nobody mistakes it for a cross-family check).
STRICT_CROSS_FAMILY = _bool("CRAB_STRICT_CROSS_FAMILY", False)
REVIEW_FALLBACK_SECONDS = _int("CRAB_REVIEW_FALLBACK_SECONDS", 90)

CLAIM_TTL_SECONDS = _int("CRAB_CLAIM_TTL_SECONDS", 900)
MAX_ATTEMPTS = _int("CRAB_MAX_ATTEMPTS", 3)
MAX_RELEASES = _int("CRAB_MAX_RELEASES", 3)
MAX_OPEN_JOBS_PER_WALLET = _int("CRAB_MAX_OPEN_JOBS_PER_WALLET", 3)
MAX_BRIEF_CHARS = _int("CRAB_MAX_BRIEF_CHARS", 4000)
ONLINE_WINDOW_SECONDS = _int("CRAB_ONLINE_WINDOW_SECONDS", 60)
SESSION_TTL_SECONDS = _int("CRAB_SESSION_TTL_SECONDS", 7 * 24 * 3600)

PINATA_JWT = os.environ.get("PINATA_JWT", "")
