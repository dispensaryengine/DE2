"""CLI: run a delta inventory check (no full re-scrape of normalized pipeline)."""
import sys
from engine.delta import run_delta

if __name__ == "__main__":
    dispensary_id = sys.argv[1] if len(sys.argv) > 1 else None
    label = dispensary_id or "all dispensaries"
    print(f"[Delta run] target={label}")
    stats = run_delta(dispensary_id=dispensary_id)
    print("\nResult:", stats)
