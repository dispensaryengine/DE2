"""Delta pipeline: fast incremental inventory updates.

Strategy
─────────
Every menu item gets a product_hash = md5(price|sale_price|quantity|in_stock).
On each delta run for a dispensary:
  1. Scrape the full menu (unavoidable — no per-product polling for most platforms)
  2. Normalise + PEK-match incoming rows (lightweight: skips full DB re-write)
  3. Load the last inventory snapshot per mcp for this dispensary
  4. For items whose hash changed → update price_comparison_index + write new snapshot
  5. For items that disappeared → mark out-of-stock
  6. For new items → full normalization + write through to MCPs
  7. Emit an inventory_changes record for each meaningful change
  8. Push snapshots + changes to Supabase

Supported change types:
  price_increase, price_decrease, went_oos, came_back_in_stock,
  sale_started, sale_ended, quantity_decrease, quantity_increase,
  new_product, product_removed
"""
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone

from config import load_registry
from engine import db
from engine.logger import get_logger
from engine.pipeline import SCRAPERS, _effective, _brand_from_title, _uid
from engine.normalize import normalize
from engine.brands import normalize_brand
from engine.matching import extract_product_name, build_pek, canonical_title


def _product_hash(price, sale_price, quantity, in_stock) -> str:
    raw = f"{price}|{sale_price}|{quantity}|{int(bool(in_stock))}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Supabase helpers ──────────────────────────────────────────────────────────

def _sb():
    try:
        from engine.supabase_client import get_client
        return get_client()
    except Exception:
        return None


def _push_snapshots(snapshots: list[dict]):
    sb = _sb()
    if not sb or not snapshots:
        return
    for i in range(0, len(snapshots), 200):
        try:
            sb.table("inventory_snapshots").insert(snapshots[i:i+200]).execute()
        except Exception:
            pass


def _push_changes(changes: list[dict]):
    sb = _sb()
    if not sb or not changes:
        return
    for i in range(0, len(changes), 200):
        try:
            sb.table("inventory_changes").insert(changes[i:i+200]).execute()
        except Exception:
            pass


def _update_price_index(mcp_id: str, dispensary_id: str, dispensary_name: str,
                         price, sale_price, effective_price, product_url, image_url,
                         in_stock: bool):
    with db.connect() as conn:
        existing = conn.execute(
            "SELECT price_index_id FROM price_comparison_index"
            " WHERE mcp_id=? AND dispensary_id=?",
            (mcp_id, dispensary_id)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE price_comparison_index"
                " SET price=?, sale_price=?, effective_price=?, product_url=?,"
                "     image_url=?, in_stock=?"
                " WHERE mcp_id=? AND dispensary_id=?",
                (price, sale_price, effective_price, product_url, image_url,
                 int(in_stock), mcp_id, dispensary_id)
            )
        else:
            conn.execute(
                "INSERT INTO price_comparison_index("
                "price_index_id,mcp_id,dpl_id,dispensary_id,dispensary_name,"
                "normalized_brand,normalized_category,normalized_size,"
                "price,sale_price,effective_price,product_url,image_url,in_stock)"
                " SELECT ?,?,dpl_id,?,?,normalized_brand,normalized_category,normalized_size,"
                "        ?,?,?,?,?,?"
                " FROM normalized_dispensary_product_listings"
                " WHERE proposed_pek=(SELECT pek FROM master_canonical_products WHERE mcp_id=?)"
                "   AND dispensary_id=? LIMIT 1",
                (_uid(), mcp_id, dispensary_id, dispensary_name,
                 price, sale_price, effective_price, product_url, image_url, int(in_stock),
                 mcp_id, dispensary_id)
            )


def _detect_changes(mcp_id: str, dispensary_id: str, title: str,
                     old_snap: dict | None, new_data: dict,
                     batch_id: str) -> list[dict]:
    """Compare old snapshot against new scrape data, return change records."""
    changes = []
    cid = lambda: uuid.uuid4().hex

    np = new_data.get("price")
    ns = new_data.get("sale_price")
    nq = new_data.get("quantity")
    ni = new_data.get("in_stock", True)
    ne = new_data.get("effective_price") or _effective(np, ns)

    disp_name = new_data.get("dispensary_name", "")

    if old_snap is None:
        changes.append({
            "change_id": cid(),
            "dispensary_id": dispensary_id,
            "mcp_id": mcp_id,
            "dispensary_name": disp_name,
            "canonical_title": title,
            "change_type": "new_product",
            "old_effective_price": None,
            "new_effective_price": ne,
            "old_quantity": None,
            "new_quantity": nq,
            "old_in_stock": None,
            "new_in_stock": ni,
            "old_sale_active": None,
            "new_sale_active": bool(ns and np and ns < np),
            "batch_id": batch_id,
            "metadata": {"title": title},
        })
        return changes

    op = old_snap.get("price")
    os_ = old_snap.get("sale_price")
    oq = old_snap.get("quantity")
    oi = old_snap.get("in_stock", True)
    oe = old_snap.get("effective_price")
    old_sale = bool(os_ and op and os_ < op)
    new_sale = bool(ns and np and ns < np)

    if oi and not ni:
        changes.append({
            "change_id": cid(), "dispensary_id": dispensary_id, "mcp_id": mcp_id,
            "dispensary_name": disp_name, "canonical_title": title,
            "change_type": "went_oos",
            "old_effective_price": oe, "new_effective_price": ne,
            "old_quantity": oq, "new_quantity": nq,
            "old_in_stock": True, "new_in_stock": False,
            "old_sale_active": old_sale, "new_sale_active": new_sale,
            "batch_id": batch_id, "metadata": {"title": title},
        })
    elif not oi and ni:
        changes.append({
            "change_id": cid(), "dispensary_id": dispensary_id, "mcp_id": mcp_id,
            "dispensary_name": disp_name, "canonical_title": title,
            "change_type": "came_back_in_stock",
            "old_effective_price": oe, "new_effective_price": ne,
            "old_quantity": oq, "new_quantity": nq,
            "old_in_stock": False, "new_in_stock": True,
            "old_sale_active": old_sale, "new_sale_active": new_sale,
            "batch_id": batch_id, "metadata": {"title": title},
        })

    if oe is not None and ne is not None and abs(float(ne) - float(oe)) > 0.01:
        ctype = "price_increase" if float(ne) > float(oe) else "price_decrease"
        changes.append({
            "change_id": cid(), "dispensary_id": dispensary_id, "mcp_id": mcp_id,
            "dispensary_name": disp_name, "canonical_title": title,
            "change_type": ctype,
            "old_effective_price": oe, "new_effective_price": ne,
            "old_quantity": oq, "new_quantity": nq,
            "old_in_stock": oi, "new_in_stock": ni,
            "old_sale_active": old_sale, "new_sale_active": new_sale,
            "batch_id": batch_id,
            "metadata": {"title": title, "delta": float(ne) - float(oe)},
        })

    if old_sale != new_sale:
        ctype = "sale_started" if new_sale else "sale_ended"
        changes.append({
            "change_id": cid(), "dispensary_id": dispensary_id, "mcp_id": mcp_id,
            "dispensary_name": disp_name, "canonical_title": title,
            "change_type": ctype,
            "old_effective_price": oe, "new_effective_price": ne,
            "old_quantity": oq, "new_quantity": nq,
            "old_in_stock": oi, "new_in_stock": ni,
            "old_sale_active": old_sale, "new_sale_active": new_sale,
            "batch_id": batch_id, "metadata": {"title": title},
        })

    if oq is not None and nq is not None and oq != nq:
        ctype = "quantity_decrease" if nq < oq else "quantity_increase"
        changes.append({
            "change_id": cid(), "dispensary_id": dispensary_id, "mcp_id": mcp_id,
            "dispensary_name": disp_name, "canonical_title": title,
            "change_type": ctype,
            "old_effective_price": oe, "new_effective_price": ne,
            "old_quantity": oq, "new_quantity": nq,
            "old_in_stock": oi, "new_in_stock": ni,
            "old_sale_active": old_sale, "new_sale_active": new_sale,
            "batch_id": batch_id, "metadata": {"title": title, "qty_delta": nq - oq},
        })

    return changes


# ── main delta run ─────────────────────────────────────────────────────────────

def run_delta(dispensary_id: str | None = None, log=print) -> dict:
    """Run a delta inventory check for one or all dispensaries.

    Returns summary dict with counts of changes detected.
    """
    batch_id = datetime.now(timezone.utc).strftime("delta_%Y%m%d_%H%M%S")
    logger = get_logger(batch_id)
    registry = load_registry()

    if dispensary_id:
        registry = [r for r in registry if r["id"] == dispensary_id]
        if not registry:
            log(f"  ! dispensary {dispensary_id} not found in registry")
            return {}

    total_changes = total_snapshots = total_oos = 0

    # load brand gazetteer for backfill
    with db.connect() as conn:
        brand_rows = conn.execute(
            "SELECT DISTINCT normalized_brand FROM normalized_dispensary_product_listings"
            " WHERE normalized_brand IS NOT NULL AND normalized_brand != ''"
        ).fetchall()
    brand_set = {r[0].lower(): r[0] for r in brand_rows}
    known_brands = sorted(brand_set.items(), key=lambda kv: -len(kv[0]))

    for disp in registry:
        did = disp["id"]
        dname = disp["name"]
        t0 = time.monotonic()
        logger.scrape_start(did, dname)

        scraper = SCRAPERS.get(disp["platform"])
        if not scraper:
            continue
        try:
            rows = scraper.scrape(disp)
        except Exception as exc:
            logger.scrape_error(did, dname, exc)
            log(f"  ! {dname}: {exc}")
            _update_schedule(did, failed=True)
            continue

        ms = int((time.monotonic() - t0) * 1000)
        logger.scrape_done(did, dname, len(rows), ms)
        log(f"  ↻ {dname}: {len(rows)} rows [{ms}ms]")

        # load last snapshot per (dispensary, mcp)
        with db.connect() as conn:
            prev = conn.execute(
                "SELECT p.mcp_id, p.price, p.sale_price, p.effective_price,"
                "       p.in_stock, m.canonical_title, m.pek"
                " FROM price_comparison_index p"
                " JOIN master_canonical_products m ON m.mcp_id = p.mcp_id"
                " WHERE p.dispensary_id = ?",
                (did,)
            ).fetchall()
        last_by_pek: dict[str, dict] = {
            r["pek"]: {
                "mcp_id": r["mcp_id"],
                "price": r["price"],
                "sale_price": r["sale_price"],
                "effective_price": r["effective_price"],
                "in_stock": bool(r["in_stock"]),
                "title": r["canonical_title"],
            }
            for r in prev
        }
        seen_peks: set[str] = set()
        new_snapshots: list[dict] = []
        new_changes: list[dict] = []

        for r in rows:
            # normalise
            raw_d = {
                "title": r.get("title"),
                "brand": r.get("brand"),
                "category": r.get("category"),
                "weight_raw": r.get("weight_raw"),
                "thc_raw": r.get("thc_raw"),
            }
            n = normalize(raw_d)
            if n["comparison_status"] != "eligible_cannabis":
                continue

            cbrand, conf = normalize_brand(r.get("brand"))
            if not cbrand:
                cbrand = _brand_from_title(n["normalized_title"], known_brands)
            n["normalized_brand"] = cbrand

            pname = extract_product_name(n["normalized_title"], cbrand)
            pek = build_pek(n, pname)
            if not pek:
                continue

            price = r.get("price")
            sale = r.get("sale_price")
            qty = r.get("quantity")
            in_stk = r.get("in_stock", True)
            if qty == 0:
                in_stk = False
            eff = _effective(price, sale)
            if eff is None:
                continue

            phash = _product_hash(price, sale, qty, in_stk)
            seen_peks.add(pek)

            # look up existing MCP by PEK
            with db.connect() as conn:
                mcp_row = conn.execute(
                    "SELECT mcp_id, canonical_title FROM master_canonical_products WHERE pek=?",
                    (pek,)
                ).fetchone()

            if not mcp_row:
                # brand-new product — emit new_product change; skip full index write here
                # (full pipeline will pick it up on next run_pipeline call)
                logger.info("mcp_created", f"New product found: {n['normalized_title']}",
                            dispensary_id=did)
                continue

            mcp_id = mcp_row["mcp_id"]
            title = mcp_row["canonical_title"]
            old_snap = last_by_pek.get(pek)

            if old_snap is None or old_snap.get("in_stock") != in_stk or \
               abs(float(eff) - float(old_snap.get("effective_price") or 0)) > 0.01 or \
               (qty is not None and qty != old_snap.get("quantity")):

                # update price index
                _update_price_index(
                    mcp_id, did, dname, price, sale, eff,
                    r.get("product_url"), r.get("image_url"), in_stk
                )

                # detect and record changes
                changes = _detect_changes(
                    mcp_id, did, title, old_snap,
                    {**r, "effective_price": eff, "dispensary_name": dname},
                    batch_id
                )
                new_changes.extend(changes)
                for c in changes:
                    logger.change_detected(did, mcp_id, c["change_type"], c["metadata"] or {})

                # snapshot
                new_snapshots.append({
                    "snapshot_id": uuid.uuid4().hex,
                    "batch_id": batch_id,
                    "dispensary_id": did,
                    "mcp_id": mcp_id,
                    "quantity": qty,
                    "in_stock": in_stk,
                    "price": price,
                    "sale_price": sale,
                    "effective_price": eff,
                    "product_hash": phash,
                    "recorded_at": _now(),
                })

        # products that disappeared from the menu → mark OOS
        active_peks = {r["pek"] for r in prev if r["pek"]}
        gone = active_peks - seen_peks
        for pek in gone:
            old = last_by_pek.get(pek)
            if not old or not old.get("in_stock"):
                continue
            mcp_id = old["mcp_id"]
            _update_price_index(old["mcp_id"], did, dname,
                                 old["price"], old.get("sale_price"),
                                 old["effective_price"], None, None, False)
            new_changes.append({
                "change_id": uuid.uuid4().hex,
                "dispensary_id": did, "mcp_id": mcp_id,
                "dispensary_name": dname, "canonical_title": old["title"],
                "change_type": "went_oos",
                "old_effective_price": old["effective_price"],
                "new_effective_price": old["effective_price"],
                "old_quantity": old.get("quantity"), "new_quantity": 0,
                "old_in_stock": True, "new_in_stock": False,
                "old_sale_active": None, "new_sale_active": None,
                "batch_id": batch_id, "metadata": {"reason": "disappeared_from_menu"},
            })
            total_oos += 1

        _push_snapshots(new_snapshots)
        _push_changes(new_changes)
        _update_schedule(did, count=len(rows), delta_run=True)

        total_snapshots += len(new_snapshots)
        total_changes += len(new_changes)

        if new_changes:
            log(f"    → {len(new_changes)} changes, {len(new_snapshots)} snapshots")

    result = {
        "batch_id": batch_id,
        "dispensaries": len(registry),
        "total_changes": total_changes,
        "total_snapshots": total_snapshots,
        "went_oos": total_oos,
    }
    logger.info("delta_run", f"Delta complete: {total_changes} changes across {len(registry)} dispensaries",
                payload=result, source="delta.run_delta")
    logger.flush()
    log(f"\nDelta complete: {total_changes} changes, {total_oos} went OOS")
    return result


def _update_schedule(dispensary_id: str, count: int = 0,
                     failed: bool = False, delta_run: bool = False):
    sb = _sb()
    if not sb:
        return
    now = _now()
    try:
        if failed:
            sb.table("scrape_schedules").upsert({
                "dispensary_id": dispensary_id,
                "consecutive_failures": 1,
                "updated_at": now,
            }, on_conflict="dispensary_id").execute()
        else:
            update = {
                "dispensary_id": dispensary_id,
                "last_product_count": count,
                "consecutive_failures": 0,
                "updated_at": now,
            }
            if delta_run:
                update["last_delta_check"] = now
            else:
                update["last_full_scrape"] = now
            sb.table("scrape_schedules").upsert(update, on_conflict="dispensary_id").execute()
    except Exception:
        pass
