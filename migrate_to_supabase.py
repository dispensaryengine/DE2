"""
Migrate all SQLite data to Supabase, enriching dispensaries with
full business info from the YAY CSV.
"""
import csv
import json
import sqlite3
import sys
import time
from pathlib import Path

from supabase import create_client, Client

# ── config ────────────────────────────────────────────────────────────────────
SUPABASE_URL = "https://azwdbrtfykocwazbktpc.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImF6d2RicnRmeWtvY3dhemJrdHBjIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODI0MzU1NDgsImV4cCI6MjA5ODAxMTU0OH0.alQnSPlZphCKiT3y5gL8Y6vB837c1XFLrFyuKBjnJAs"

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "engine.db"
REGISTRY_PATH = DATA_DIR / "registry.json"
CSV_PATH = Path(__file__).resolve().parent.parent / "YAY - Sheet1.csv"

BATCH = 500

# ── helpers ───────────────────────────────────────────────────────────────────
def to_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def batch_insert(sb: Client, table: str, rows: list, label: str):
    if not rows:
        print(f"  {label}: 0 rows, skipping")
        return
    errors = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        try:
            sb.table(table).insert(chunk).execute()
        except Exception as exc:
            errors += 1
            print(f"  {label} chunk {i//BATCH}: ERROR {exc}")
        if i % (BATCH * 10) == 0 and i > 0:
            print(f"  {label}: {i}/{len(rows)} inserted...")
            time.sleep(0.1)
    status = "OK" if errors == 0 else f"{errors} ERRORS"
    print(f"  {label}: {len(rows)} rows [{status}]")


# ── load CSV business info ────────────────────────────────────────────────────
def load_yay_csv():
    """Returns dict keyed by dispensary name (lower) -> business info."""
    info = {}
    with open(CSV_PATH, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = row["Dispensary"].strip()
            # parse address → city/state/zip
            raw_addr = row.get("Address", "").strip().strip('"')
            parts = [p.strip() for p in raw_addr.split(",")]
            address = parts[0] if parts else raw_addr
            city = parts[1] if len(parts) > 1 else None
            state_zip = parts[2].strip() if len(parts) > 2 else ""
            sz_parts = state_zip.split()
            state = sz_parts[0] if sz_parts else None
            zip_code = sz_parts[1] if len(sz_parts) > 1 else None

            info[name.lower()] = {
                "address": address,
                "city": city,
                "state": state,
                "zip": zip_code,
                "phone": row.get("Phone", "").strip() or None,
                "website_url": row.get("Website", "").strip() or None,
                "menu_url": row.get("Menu URL", "").strip() or None,
                "specials_url": row.get("Specials URL", "").strip() or None,
            }
    print(f"  CSV: {len(info)} dispensary records loaded")
    return info


# ── build platform_config JSONB from registry ─────────────────────────────────
PLATFORM_KEYS = {
    "blaze": ["blaze_store_id"],
    "carrot": ["carrot_space_id", "carrot_location_id", "carrot_api_base"],
    "dispense": ["dispense_venue_id"],
    "dutchie": ["dutchie_slug"],
    "goodlife": ["goodlife_location_id", "goodlife_api"],
    "jane": [],
    "kushmart": ["kushmart_url"],
    "proteus": ["proteus_type", "proteus_host"],
    "weedmaps": ["weedmaps_slug"],
}


def platform_config(reg: dict) -> dict:
    keys = PLATFORM_KEYS.get(reg.get("platform", ""), [])
    return {k: reg[k] for k in keys if k in reg}


# ── main migration ─────────────────────────────────────────────────────────────
def main():
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    yay = load_yay_csv()
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    sqlite_conn = sqlite3.connect(DB_PATH)
    sqlite_conn.row_factory = sqlite3.Row

    print("\n[1/7] Dispensaries ...")
    disp_rows = []
    for r in registry:
        if not r.get("enabled", True):
            continue
        name = r["name"]
        biz = yay.get(name.lower(), {})
        # fuzzy match: try partial
        if not biz:
            for k, v in yay.items():
                if k in name.lower() or name.lower() in k:
                    biz = v
                    break
        cfg = platform_config(r)
        disp_rows.append({
            "dispensary_id": r["id"],
            "name": name,
            "platform": r["platform"],
            "address": biz.get("address"),
            "city": biz.get("city"),
            "state": biz.get("state"),
            "zip": biz.get("zip"),
            "phone": biz.get("phone"),
            "website_url": biz.get("website_url") or r.get("url"),
            "menu_url": biz.get("menu_url"),
            "specials_url": biz.get("specials_url"),
            "external_id": r.get("external_id"),
            "platform_config": cfg if cfg else None,
            "enabled": True,
        })
    batch_insert(sb, "dispensaries", disp_rows, "dispensaries")

    # get the batch_id from SQLite
    latest_batch = sqlite_conn.execute(
        "SELECT batch_id, MAX(scraped_at) FROM raw_dispensary_product_listings"
    ).fetchone()
    batch_id = latest_batch[0] if latest_batch else "migration_batch"
    scrape_time = latest_batch[1] if latest_batch else None

    print(f"\n[2/7] Pipeline batch record (batch_id={batch_id}) ...")
    disp_count = sqlite_conn.execute("SELECT COUNT(*) FROM dispensaries").fetchone()[0]
    raw_count = sqlite_conn.execute("SELECT COUNT(*) FROM raw_dispensary_product_listings").fetchone()[0]
    elig_count = sqlite_conn.execute(
        "SELECT COUNT(*) FROM normalized_dispensary_product_listings WHERE comparison_status='eligible_cannabis'"
    ).fetchone()[0]
    mcp_count = sqlite_conn.execute("SELECT COUNT(*) FROM master_canonical_products").fetchone()[0]
    idx_count = sqlite_conn.execute("SELECT COUNT(*) FROM price_comparison_index").fetchone()[0]
    multi = sqlite_conn.execute(
        "SELECT COUNT(*) FROM (SELECT mcp_id FROM price_comparison_index GROUP BY mcp_id HAVING COUNT(*)>1)"
    ).fetchone()[0]
    batch_row = {
        "batch_id": batch_id,
        "started_at": scrape_time,
        "completed_at": scrape_time,
        "dispensaries_scraped": disp_count,
        "raw_listings": raw_count,
        "eligible_listings": elig_count,
        "canonical_products": mcp_count,
        "priced_listings": idx_count,
        "multi_shop_products": multi,
        "status": "completed",
    }
    try:
        sb.table("pipeline_batches").insert(batch_row).execute()
        print(f"  pipeline_batches: 1 row [OK]")
    except Exception as e:
        print(f"  pipeline_batches: ERROR {e}")

    print("\n[3/7] Raw archive ...")
    raws = sqlite_conn.execute("SELECT * FROM raw_dispensary_product_listings").fetchall()
    raw_rows = []
    for r in raws:
        raw_rows.append({
            "raw_dpl_id": r["raw_dpl_id"],
            "batch_id": r["batch_id"],
            "dispensary_id": r["dispensary_id"],
            "source_platform": r["source_platform"],
            "source_product_title": r["source_product_title"],
            "source_brand": r["source_brand"],
            "source_category": r["source_category"],
            "source_subcategory": r["source_subcategory"],
            "price": to_float(r["price"]),
            "sale_price": to_float(r["sale_price"]),
            "thc_raw": r["thc_raw"],
            "cbd_raw": r["cbd_raw"],
            "weight_raw": str(r["weight_raw"]) if r["weight_raw"] is not None else None,
            "product_url": r["product_url"],
            "image_url": r["image_url"],
            "description": r["description"],
            "raw_payload": json.loads(r["raw_payload"]) if r["raw_payload"] else None,
            "scraped_at": r["scraped_at"],
        })
    batch_insert(sb, "raw_dispensary_product_listings", raw_rows, "raw archive")

    print("\n[4/7] Normalized listings ...")
    ndpls = sqlite_conn.execute("SELECT * FROM normalized_dispensary_product_listings").fetchall()
    ndpl_rows = []
    for r in ndpls:
        ndpl_rows.append({
            "dpl_id": r["dpl_id"],
            "raw_dpl_id": r["raw_dpl_id"],
            "batch_id": r["batch_id"],
            "dispensary_id": r["dispensary_id"],
            "source_product_title": r["source_product_title"],
            "normalized_title": r["normalized_title"],
            "source_brand": r["source_brand"],
            "normalized_brand": r["normalized_brand"],
            "brand_confidence": to_float(r["brand_confidence"]),
            "raw_category": r["raw_category"],
            "normalized_category": r["normalized_category"],
            "normalized_form": r["normalized_form"],
            "subform": r["subform"],
            "normalized_product_name": r["normalized_product_name"],
            "size_value": to_float(r["size_value"]),
            "size_unit": r["size_unit"],
            "normalized_size": r["normalized_size"],
            "count": r["count"],
            "package_thc_mg": to_float(r["package_thc_mg"]),
            "serving_thc_mg": to_float(r["serving_thc_mg"]),
            "cannabinoid_profile": r["cannabinoid_profile"],
            "ratio": r["ratio"],
            "extract_type": r["extract_type"],
            "infusion_type": r["infusion_type"],
            "hardware_type": r["hardware_type"],
            "dominance_or_type": r["dominance_or_type"],
            "thc_value": r["thc_value"],
            "price": to_float(r["price"]),
            "sale_price": to_float(r["sale_price"]),
            "effective_price": to_float(r["effective_price"]),
            "product_url": r["product_url"],
            "image_url": r["image_url"],
            "comparison_status": r["comparison_status"],
            "proposed_pek": r["proposed_pek"],
            "extraction_confidence": to_float(r["extraction_confidence"]),
        })
    batch_insert(sb, "normalized_dispensary_product_listings", ndpl_rows, "normalized")

    print("\n[5/7] Master canonical products ...")
    mcps = sqlite_conn.execute("SELECT * FROM master_canonical_products").fetchall()
    mcp_rows = []
    for r in mcps:
        mcp_rows.append({
            "mcp_id": r["mcp_id"],
            "pek": r["pek"],
            "canonical_title": r["canonical_title"],
            "search_title": r["search_title"],
            "normalized_brand": r["normalized_brand"],
            "normalized_category": r["normalized_category"],
            "normalized_form": r["normalized_form"],
            "subform": r["subform"],
            "canonical_product_name": r["canonical_product_name"],
            "normalized_size": r["normalized_size"],
            "count": r["count"],
            "package_thc_mg": to_float(r["package_thc_mg"]),
            "cannabinoid_profile": r["cannabinoid_profile"],
            "ratio": r["ratio"],
            "extract_type": r["extract_type"],
            "infusion_type": r["infusion_type"],
            "hardware_type": r["hardware_type"],
            "dominance_or_type": r["dominance_or_type"],
            "image_url": r["image_url"],
            "review_status": r["review_status"],
        })
    batch_insert(sb, "master_canonical_products", mcp_rows, "MCPs")

    print("\n[6/7] MCP-DPL links ...")
    links = sqlite_conn.execute("SELECT * FROM mcp_dpl_links").fetchall()
    link_rows = [
        {
            "link_id": r["link_id"],
            "mcp_id": r["mcp_id"],
            "dpl_id": r["dpl_id"],
            "match_confidence": to_float(r["match_confidence"]),
            "match_method": r["match_method"],
            "needs_review": bool(r["needs_review"]),
        }
        for r in links
    ]
    batch_insert(sb, "mcp_dpl_links", link_rows, "mcp_dpl_links")

    print("\n[7/7] Price comparison index ...")
    idx = sqlite_conn.execute("SELECT * FROM price_comparison_index").fetchall()
    idx_rows = [
        {
            "price_index_id": r["price_index_id"],
            "mcp_id": r["mcp_id"],
            "dpl_id": r["dpl_id"],
            "dispensary_id": r["dispensary_id"],
            "dispensary_name": r["dispensary_name"],
            "canonical_title": r["canonical_title"],
            "normalized_brand": r["normalized_brand"],
            "normalized_category": r["normalized_category"],
            "normalized_size": r["normalized_size"],
            "price": to_float(r["price"]),
            "sale_price": to_float(r["sale_price"]),
            "effective_price": to_float(r["effective_price"]),
            "product_url": r["product_url"],
            "image_url": r["image_url"],
            "in_stock": bool(r["in_stock"]) if r["in_stock"] is not None else True,
        }
        for r in idx
    ]
    batch_insert(sb, "price_comparison_index", idx_rows, "price index")

    sqlite_conn.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    main()
