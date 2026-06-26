"""Per-platform live self-test. Scrapes one dispensary per platform and reports."""
import sys
import json
import traceback

from config import load_registry
from engine.pipeline import SCRAPERS


def sample(rows, n=3):
    out = []
    for r in rows[:n]:
        out.append({
            "title": r.get("title"),
            "brand": r.get("brand"),
            "category": r.get("category"),
            "weight_raw": r.get("weight_raw"),
            "price": r.get("price"),
            "sale_price": r.get("sale_price"),
        })
    return out


def main(platforms):
    registry = load_registry(active_only=False)
    by_platform = {}
    for r in registry:
        if not r.get("enabled", True):
            continue
        by_platform.setdefault(r["platform"], []).append(r)

    for plat in platforms:
        disps = by_platform.get(plat, [])
        print(f"\n===== {plat.upper()} ({len(disps)} enabled dispensaries) =====")
        if not disps:
            print("  (no enabled dispensaries)")
            continue
        for disp in disps:
            scraper = SCRAPERS.get(plat)
            try:
                rows = scraper.scrape(disp)
                priced = [r for r in rows if r.get("price") not in (None, 0)]
                print(f"  {disp['id']}: {len(rows)} rows, {len(priced)} priced")
                if rows:
                    print("    sample:", json.dumps(sample(rows), default=str))
            except Exception as exc:  # noqa: BLE001
                print(f"  {disp['id']}: ERROR {type(exc).__name__}: {exc}")
                traceback.print_exc()


if __name__ == "__main__":
    plats = sys.argv[1:] or [
        "proteus", "dispense", "weedmaps", "jane", "blaze", "kushmart", "goodlife",
    ]
    main(plats)
