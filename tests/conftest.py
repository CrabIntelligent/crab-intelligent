import os
import sys
import tempfile
from pathlib import Path

# Settings are read at import time, so point the server at a throwaway data dir before any import.
os.environ["CRAB_DATA_DIR"] = tempfile.mkdtemp(prefix="crab-test-")
os.environ["CRAB_DEV_MODE"] = "1"
os.environ["CRAB_REVIEW_FALLBACK_SECONDS"] = "3600"
os.environ["CRAB_MAX_OPEN_JOBS_PER_WALLET"] = "50"
os.environ["PINATA_JWT"] = ""

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
