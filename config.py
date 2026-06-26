"""Central configuration and dispensary registry loader."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "engine.db"
REGISTRY_PATH = DATA_DIR / "registry.json"

# Platforms wired in.
ACTIVE_PLATFORMS = {
    "dutchie", "carrot", "proteus", "dispense",
    "weedmaps", "jane", "blaze", "kushmart", "goodlife",
}

REQUEST_DELAY = 0.3      # polite delay between API calls (seconds)
PAGE_LIMIT = 100         # products per page where the API supports paging


def load_registry(active_only: bool = True) -> list[dict]:
    """Return dispensary records, filtered to enabled + active v1 platforms."""
    records = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    out = []
    for r in records:
        if not r.get("enabled", True):
            continue
        if active_only and r.get("platform") not in ACTIVE_PLATFORMS:
            continue
        out.append(r)
    return out
