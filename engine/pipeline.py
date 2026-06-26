"""End-to-end pipeline: scrape -> raw -> normalize -> PEK/MCP -> price index."""
import json
import time
import uuid
from datetime import datetime, timezone

from config import load_registry
from engine import db
from engine.logger import get_logger
from engine.scrapers import (
    dutchie, carrot, proteus, dispense, weedmaps, jane, blaze, kushmart, goodlife,
)
from engine.normalize import normalize
from engine.brands import normalize_brand, brand_key
from engine.matching import extract_product_name, build_pek, canonical_title

SCRAPERS = {
    "dutchie": dutchie,
    "carrot": carrot,
    "proteus": proteus,
    "dispense": dispense,
    "weedmaps": weedmaps,
    "jane": jane,
    "blaze": blaze,
    "kushmart": kushmart,
    "goodlife": goodlife,
}


def _uid():
    return uuid.uuid4().hex


def _effective(price, sale):
    vals = [p for p in (price, sale) if p is not None and p > 0]
    return min(vals) if vals else None


def scrape_all(registry=None, log=print):
    """Scrape every active dispensary, store raw rows. Returns batch_id."""
    db.init_db()
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    registry = registry if registry is not None else load_registry()
    now = datetime.now(timezone.utc).isoformat()

    with db.connect() as conn:
        for disp in registry:
            conn.execute(
                "INSERT OR REPLACE INTO dispensaries(dispensary_id,name,platform,url,address)"
                " VALUES(?,?,?,?,?)",
                (disp["id"], disp["name"], disp["platform"], disp.get("url"), disp.get("address")),
            )

    logger = get_logger(batch_id)
    for disp in registry:
        scraper = SCRAPERS.get(disp["platform"])
        if not scraper:
            continue
        logger.scrape_start(disp["id"], disp["name"])
        t0 = time.monotonic()
        try:
            rows = scraper.scrape(disp)
        except Exception as exc:  # noqa: BLE001
            logger.scrape_error(disp["id"], disp["name"], exc)
            log(f"  ! {disp['name']}: scrape failed ({exc})")
            continue
        ms = int((time.monotonic() - t0) * 1000)
        logger.scrape_done(disp["id"], disp["name"], len(rows), ms)
        log(f"  + {disp['name']}: {len(rows)} listings")
        with db.connect() as conn:
            for r in rows:
                conn.execute(
                    "INSERT INTO raw_dispensary_product_listings("
                    "raw_dpl_id,batch_id,dispensary_id,source_platform,source_product_title,"
                    "source_brand,source_category,source_subcategory,price,sale_price,thc_raw,"
                    "cbd_raw,weight_raw,product_url,image_url,description,raw_payload,"
                    "quantity,in_stock,scraped_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (_uid(), batch_id, disp["id"], disp["platform"], r.get("title"),
                     r.get("brand"), r.get("category"), r.get("subcategory"), r.get("price"),
                     r.get("sale_price"), r.get("thc_raw"), r.get("cbd_raw"), r.get("weight_raw"),
                     r.get("product_url"), r.get("image_url"), r.get("description"),
                     json.dumps(r.get("raw_payload"), default=str),
                     r.get("quantity"), int(r.get("in_stock", True)), now),
                )
    logger.flush(timeout=2.0)
    return batch_id


def _brand_from_title(title, known_brands):
    """Detect a known brand appearing at the start of `title`. Prefix-only
    matching keeps this safe (brands lead the title on Blaze/Proteus/etc.)."""
    if not title:
        return None
    t = title.lower()
    for bl, bc in known_brands:
        if t == bl or (t.startswith(bl) and not t[len(bl)].isalnum()):
            return bc
    return None


def normalize_all(log=print):
    """Normalize all raw rows into normalized DPLs + PEK.

    Two passes: pass 1 normalizes and harvests every known brand; pass 2
    backfills brand-less eligible rows by matching a known brand at the
    start of the title (recovers brands for platforms that don't expose a
    brand field, e.g. Blaze and Proteus)."""
    with db.connect() as conn:
        raws = conn.execute("SELECT * FROM raw_dispensary_product_listings").fetchall()

    interim = []
    brand_set = {}
    for raw in raws:
        raw_d = {
            "title": raw["source_product_title"],
            "brand": raw["source_brand"],
            "category": raw["source_category"],
            "weight_raw": raw["weight_raw"],
            "thc_raw": raw["thc_raw"],
        }
        n = normalize(raw_d)
        cbrand, conf = normalize_brand(raw["source_brand"])
        if cbrand and len(cbrand) >= 3:
            brand_set[cbrand.lower()] = cbrand
        interim.append((raw, raw_d, n, cbrand, conf))

    known_brands = sorted(brand_set.items(), key=lambda kv: -len(kv[0]))
    backfilled = 0

    rows = []
    for raw, raw_d, n, cbrand, conf in interim:
        if not cbrand and n["comparison_status"] == "eligible_cannabis":
            guess = _brand_from_title(n["normalized_title"], known_brands)
            if guess:
                cbrand, conf, backfilled = guess, 0.70, backfilled + 1
        n["normalized_brand"] = cbrand
        pname = None
        pek = None
        if n["comparison_status"] == "eligible_cannabis":
            pname = extract_product_name(n["normalized_title"], cbrand)
            pek = build_pek(n, pname)
        rows.append((raw, raw_d, n, cbrand, conf, pname, pek))
    log(f"  brand backfill from title: {backfilled} listings")

    with db.connect() as conn:
        for raw, raw_d, n, cbrand, conf, pname, pek in rows:
            conn.execute(
                "INSERT INTO normalized_dispensary_product_listings("
                "dpl_id,raw_dpl_id,batch_id,dispensary_id,source_product_title,normalized_title,"
                "source_brand,normalized_brand,brand_confidence,raw_category,normalized_category,"
                "normalized_form,subform,normalized_product_name,size_value,size_unit,normalized_size,"
                "count,package_thc_mg,serving_thc_mg,cannabinoid_profile,ratio,extract_type,"
                "infusion_type,hardware_type,dominance_or_type,thc_value,price,sale_price,"
                "effective_price,product_url,image_url,comparison_status,proposed_pek,extraction_confidence)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_uid(), raw["raw_dpl_id"], raw["batch_id"], raw["dispensary_id"],
                 raw["source_product_title"], n["normalized_title"], raw["source_brand"],
                 cbrand, conf, raw["source_category"], n["normalized_category"],
                 n["normalized_form"], n["subform"], pname, n["size_value"], n["size_unit"],
                 n["normalized_size"], n["count"], n["package_thc_mg"], n["serving_thc_mg"],
                 n["cannabinoid_profile"], n["ratio"], n["extract_type"], n["infusion_type"],
                 n["hardware_type"], n["dominance_or_type"], raw["thc_raw"], raw["price"],
                 raw["sale_price"], _effective(raw["price"], raw["sale_price"]),
                 raw["product_url"], raw["image_url"], n["comparison_status"], pek, conf),
            )
    eligible = sum(1 for r in rows if r[2]["comparison_status"] == "eligible_cannabis")
    log(f"  normalized {len(rows)} listings ({eligible} cannabis-eligible)")


def build_mcps_and_index(log=print):
    """Group eligible DPLs by PEK into MCPs and build the price comparison index."""
    with db.connect() as conn:
        dpls = conn.execute(
            "SELECT n.*, d.name AS dispensary_name FROM normalized_dispensary_product_listings n"
            " JOIN dispensaries d ON d.dispensary_id = n.dispensary_id"
            " WHERE n.comparison_status='eligible_cannabis' AND n.proposed_pek IS NOT NULL"
            " AND n.proposed_pek != '' AND n.effective_price IS NOT NULL"
        ).fetchall()

    pek_to_mcp = {}
    mcp_rows, link_rows, index_rows = [], [], []

    for d in dpls:
        pek = d["proposed_pek"]
        if pek not in pek_to_mcp:
            mcp_id = _uid()
            pek_to_mcp[pek] = mcp_id
            n = dict(d)
            title = canonical_title(n, d["normalized_product_name"])
            mcp_rows.append((
                mcp_id, pek, title or d["normalized_title"],
                (title or d["normalized_title"] or "").lower(),
                d["normalized_brand"], d["normalized_category"], d["normalized_form"],
                d["subform"], d["normalized_product_name"], d["normalized_size"], d["count"],
                d["package_thc_mg"], d["cannabinoid_profile"], d["ratio"], d["extract_type"],
                d["infusion_type"], d["hardware_type"], d["dominance_or_type"], d["image_url"],
                "approved",
            ))
        mcp_id = pek_to_mcp[pek]
        link_rows.append((_uid(), mcp_id, d["dpl_id"], 100.0, "exact_pek", 0))
        index_rows.append((
            _uid(), mcp_id, d["dpl_id"], d["dispensary_id"], d["dispensary_name"],
            None, d["normalized_brand"], d["normalized_category"], d["normalized_size"],
            d["price"], d["sale_price"], d["effective_price"], d["product_url"],
            d["image_url"], 1,
        ))

    # Dedupe to one priced row per (product, dispensary): keep lowest effective price.
    best = {}
    for r in index_rows:
        key = (r[1], r[3])  # (mcp_id, dispensary_id)
        eff = r[11]
        if key not in best or (eff is not None and eff < best[key][11]):
            best[key] = r
    index_rows = list(best.values())

    # backfill canonical_title onto index rows
    mcp_title = {m[0]: m[2] for m in mcp_rows}
    index_rows = [
        (r[0], r[1], r[2], r[3], r[4], mcp_title.get(r[1]), *r[6:]) for r in index_rows
    ]

    with db.connect() as conn:
        conn.executemany(
            "INSERT INTO master_canonical_products(mcp_id,pek,canonical_title,search_title,"
            "normalized_brand,normalized_category,normalized_form,subform,canonical_product_name,"
            "normalized_size,count,package_thc_mg,cannabinoid_profile,ratio,extract_type,"
            "infusion_type,hardware_type,dominance_or_type,image_url,review_status)"
            " VALUES(" + ",".join("?" * 20) + ")", mcp_rows)
        conn.executemany(
            "INSERT INTO mcp_dpl_links(link_id,mcp_id,dpl_id,match_confidence,match_method,needs_review)"
            " VALUES(?,?,?,?,?,?)", link_rows)
        conn.executemany(
            "INSERT INTO price_comparison_index(price_index_id,mcp_id,dpl_id,dispensary_id,"
            "dispensary_name,canonical_title,normalized_brand,normalized_category,normalized_size,"
            "price,sale_price,effective_price,product_url,image_url,in_stock)"
            " VALUES(" + ",".join("?" * 15) + ")", index_rows)

    # count products carried by 2+ distinct dispensaries (from the deduped index)
    shops_per_mcp = {}
    for r in index_rows:
        shops_per_mcp.setdefault(r[1], set()).add(r[3])
    multi = sum(1 for s in shops_per_mcp.values() if len(s) > 1)
    log(f"  {len(mcp_rows)} canonical products, {len(index_rows)} priced listings, "
        f"{multi} products at 2+ dispensaries")
    return {"mcps": len(mcp_rows), "listings": len(index_rows), "multi": multi}


def run(reset=True, log=print):
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logger = get_logger(batch_id)
    t0 = time.monotonic()

    db.init_db()
    if reset:
        db.reset_pipeline_tables()
        logger.info("pipeline_reset", "Pipeline tables reset for fresh run",
                    source="pipeline.run")

    logger.info("pipeline_start", f"Full pipeline run starting (batch {batch_id})",
                source="pipeline.run")
    log("[1/3] Scraping menus ...")
    scrape_all(log=log)
    log("[2/3] Normalizing ...")
    normalize_all(log=log)
    log("[3/3] Matching + price index ...")
    stats = build_mcps_and_index(log=log)

    ms = int((time.monotonic() - t0) * 1000)
    logger.info("pipeline_complete",
                f"Pipeline complete: {stats.get('mcps',0)} MCPs, "
                f"{stats.get('listings',0)} listings in {ms//1000}s",
                payload={**stats, "duration_ms": ms},
                source="pipeline.run")
    logger.flush()
    return stats
