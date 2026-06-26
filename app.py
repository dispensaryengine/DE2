"""FastAPI app: product search + comparison + dispensary profiles + inventory monitoring."""
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import ROOT
from engine import db
from engine.supabase_client import get_client
from engine.embeddings import get_agent

app = FastAPI(title="WNY Cannabis Price Comparison Engine")


# ── existing SQLite-backed endpoints ──────────────────────────────────────────

@app.get("/api/search")
def search(q: str = Query("", min_length=0), category: str | None = None,
           brand: str | None = None, limit: int = 50):
    """Return canonical products matching the query, with price spread."""
    terms = [t for t in q.lower().split() if t]
    where = ["1=1"]
    params: list = []
    for t in terms:
        where.append("LOWER(m.canonical_title) LIKE ?")
        params.append(f"%{t}%")
    if category:
        where.append("m.normalized_category = ?")
        params.append(category)
    if brand:
        where.append("m.normalized_brand = ?")
        params.append(brand)

    sql = (
        "SELECT m.mcp_id, m.canonical_title, m.normalized_brand, m.normalized_category,"
        " m.normalized_size, m.image_url,"
        " COUNT(p.price_index_id) AS offering_count,"
        " MIN(p.effective_price) AS price_min,"
        " MAX(p.effective_price) AS price_max"
        " FROM master_canonical_products m"
        " JOIN price_comparison_index p ON p.mcp_id = m.mcp_id"
        " WHERE " + " AND ".join(where) +
        " GROUP BY m.mcp_id"
        " ORDER BY offering_count DESC, price_min ASC"
        " LIMIT ?"
    )
    params.append(limit)
    with db.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return {"count": len(rows), "results": [dict(r) for r in rows]}


@app.get("/api/compare/{mcp_id}")
def compare(mcp_id: str):
    """Side-by-side price comparison for one canonical product."""
    with db.connect() as conn:
        mcp = conn.execute(
            "SELECT * FROM master_canonical_products WHERE mcp_id=?", (mcp_id,)
        ).fetchone()
        offers = conn.execute(
            "SELECT dispensary_name, dispensary_id, price, sale_price, effective_price,"
            " product_url, in_stock"
            " FROM price_comparison_index WHERE mcp_id=? ORDER BY effective_price ASC",
            (mcp_id,),
        ).fetchall()
    if not mcp:
        raise HTTPException(status_code=404, detail="product not found")
    offers = [dict(o) for o in offers]
    prices = [o["effective_price"] for o in offers if o["effective_price"] is not None]
    return {
        "product": dict(mcp),
        "offer_count": len(offers),
        "price_min": min(prices) if prices else None,
        "price_max": max(prices) if prices else None,
        "best_value": offers[0] if offers else None,
        "offers": offers,
    }


@app.get("/api/stats")
def stats():
    with db.connect() as conn:
        def one(sql):
            return conn.execute(sql).fetchone()[0]
        return {
            "dispensaries": one("SELECT COUNT(*) FROM dispensaries"),
            "canonical_products": one("SELECT COUNT(*) FROM master_canonical_products"),
            "priced_listings": one("SELECT COUNT(*) FROM price_comparison_index"),
            "multi_dispensary": one(
                "SELECT COUNT(*) FROM (SELECT mcp_id FROM price_comparison_index"
                " GROUP BY mcp_id HAVING COUNT(*) > 1)"),
            "in_stock": one("SELECT COUNT(*) FROM price_comparison_index WHERE in_stock=1"),
            "on_sale": one(
                "SELECT COUNT(*) FROM price_comparison_index"
                " WHERE sale_price IS NOT NULL AND sale_price < price"),
        }


# ── semantic search (ChromaDB) ────────────────────────────────────────────────

@app.get("/api/search/semantic")
def semantic_search(q: str = Query(..., min_length=2), n: int = 20,
                    category: str | None = None):
    """Semantic search via Supabase pgvector — finds by meaning, not just keywords."""
    # generate query embedding locally
    agent = get_agent()
    if not agent._check_available():
        raise HTTPException(status_code=503, detail="Embedding index not built yet")
    vectors = agent._encode([q.lower()])
    if not vectors:
        raise HTTPException(status_code=503, detail="Could not encode query")

    # call the Supabase semantic_search_products function
    sb = get_client()
    try:
        resp = sb.rpc("semantic_search_products", {
            "query_embedding": vectors[0],
            "match_count": n,
            "filter_category": category,
            "filter_brand": None,
            "min_similarity": 0.3,
        }).execute()
        results = resp.data or []
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Supabase search error: {exc}")

    if not results:
        return {"count": 0, "results": []}

    # enrich with live pricing from SQLite
    mcp_ids = [r["mcp_id"] for r in results]
    placeholders = ",".join("?" * len(mcp_ids))
    with db.connect() as conn:
        pricing = {
            row["mcp_id"]: row for row in conn.execute(
                f"SELECT mcp_id, COUNT(*) AS shops, MIN(effective_price) AS price_min,"
                f" MAX(effective_price) AS price_max"
                f" FROM price_comparison_index WHERE mcp_id IN ({placeholders})"
                f" GROUP BY mcp_id",
                mcp_ids
            ).fetchall()
        }
    for r in results:
        # normalise field name from Supabase RPC ("similarity") to public API ("score")
        if "similarity" in r and "score" not in r:
            r["score"] = round(float(r.pop("similarity")), 4)
        p = pricing.get(r["mcp_id"])
        r["offering_count"] = p["shops"] if p else 0
        r["price_min"] = float(p["price_min"]) if p and p["price_min"] else None
        r["price_max"] = float(p["price_max"]) if p and p["price_max"] else None
    return {"count": len(results), "results": results}


# ── inventory monitoring endpoints ───────────────────────────────────────────

@app.get("/api/inventory/changes")
def recent_changes(dispensary_id: str | None = None, limit: int = 50,
                   change_type: str | None = None):
    """Recent inventory changes (price moves, OOS events, restocks)."""
    sb = get_client()
    q = (
        sb.table("inventory_changes")
        .select(
            "change_id,dispensary_id,dispensary_name,canonical_title,"
            "change_type,old_effective_price,new_effective_price,price_delta,"
            "old_in_stock,new_in_stock,old_sale_active,new_sale_active,"
            "old_quantity,new_quantity,detected_at"
        )
        .order("detected_at", desc=True)
        .limit(limit)
    )
    if dispensary_id:
        q = q.eq("dispensary_id", dispensary_id)
    if change_type:
        q = q.eq("change_type", change_type)
    result = q.execute()
    return {"count": len(result.data), "changes": result.data}


@app.get("/api/inventory/history/{mcp_id}")
def product_inventory_history(mcp_id: str, dispensary_id: str | None = None,
                               limit: int = 100):
    """Full inventory history for one product across all dispensaries."""
    sb = get_client()
    q = (
        sb.table("inventory_snapshots")
        .select(
            "snapshot_id,dispensary_id,in_stock,quantity,effective_price,"
            "sale_active,product_hash,recorded_at"
        )
        .eq("mcp_id", mcp_id)
        .order("recorded_at", desc=True)
        .limit(limit)
    )
    if dispensary_id:
        q = q.eq("dispensary_id", dispensary_id)
    result = q.execute()

    # get product info
    with db.connect() as conn:
        mcp = conn.execute(
            "SELECT canonical_title, normalized_brand, normalized_category"
            " FROM master_canonical_products WHERE mcp_id=?", (mcp_id,)
        ).fetchone()

    return {
        "mcp_id": mcp_id,
        "product": dict(mcp) if mcp else None,
        "snapshot_count": len(result.data),
        "history": result.data,
    }


@app.get("/api/inventory/oos")
def out_of_stock(dispensary_id: str | None = None, limit: int = 50):
    """Products currently marked out of stock."""
    with db.connect() as conn:
        if dispensary_id:
            rows = conn.execute(
                "SELECT p.mcp_id, p.dispensary_id, p.dispensary_name, p.canonical_title,"
                " p.effective_price, p.normalized_category"
                " FROM price_comparison_index p"
                " WHERE p.in_stock=0 AND p.dispensary_id=?"
                " ORDER BY p.canonical_title LIMIT ?",
                (dispensary_id, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT p.mcp_id, p.dispensary_id, p.dispensary_name, p.canonical_title,"
                " p.effective_price, p.normalized_category"
                " FROM price_comparison_index p WHERE p.in_stock=0"
                " ORDER BY p.dispensary_name, p.canonical_title LIMIT ?",
                (limit,)
            ).fetchall()
    return {"count": len(rows), "items": [dict(r) for r in rows]}


@app.get("/api/inventory/sales")
def active_sales(limit: int = 100):
    """Products currently on sale, sorted by savings %."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT p.mcp_id, p.dispensary_name, p.canonical_title,"
            " p.normalized_category, p.price, p.sale_price, p.effective_price,"
            " ROUND((p.price - p.effective_price) / p.price * 100, 1) AS pct_off"
            " FROM price_comparison_index p"
            " WHERE p.sale_price IS NOT NULL AND p.sale_price < p.price AND p.in_stock=1"
            " ORDER BY pct_off DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return {"count": len(rows), "sales": [dict(r) for r in rows]}


# ── event log endpoint ────────────────────────────────────────────────────────

@app.get("/api/events")
def pipeline_events(severity: str | None = None, event_type: str | None = None,
                    dispensary_id: str | None = None, limit: int = 100):
    """Query the pipeline event log."""
    sb = get_client()
    q = (
        sb.table("pipeline_events")
        .select("event_id,event_type,severity,dispensary_id,message,payload,duration_ms,created_at")
        .order("created_at", desc=True)
        .limit(limit)
    )
    if severity:
        q = q.eq("severity", severity)
    if event_type:
        q = q.eq("event_type", event_type)
    if dispensary_id:
        q = q.eq("dispensary_id", dispensary_id)
    result = q.execute()
    return {"count": len(result.data), "events": result.data}


# ── Supabase-backed dispensary profile endpoints ───────────────────────────────

@app.get("/api/dispensaries")
def list_dispensaries():
    """List all enabled dispensaries with full business info."""
    sb = get_client()
    result = (
        sb.table("dispensaries")
        .select(
            "dispensary_id,name,platform,address,city,state,zip,"
            "phone,website_url,menu_url,specials_url"
        )
        .eq("enabled", True)
        .order("name")
        .execute()
    )
    # enrich each dispensary with product count from SQLite
    with db.connect() as conn:
        counts = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT dispensary_id, COUNT(*) FROM price_comparison_index"
                " GROUP BY dispensary_id"
            ).fetchall()
        }
    dispensaries = []
    for d in result.data:
        d["product_count"] = counts.get(d["dispensary_id"], 0)
        dispensaries.append(d)
    return {"count": len(dispensaries), "dispensaries": dispensaries}


@app.get("/api/dispensaries/{dispensary_id}")
def dispensary_profile(dispensary_id: str):
    """Full dispensary profile: business info + their normalized products by category."""
    sb = get_client()

    # get business info from Supabase
    result = (
        sb.table("dispensaries")
        .select("*")
        .eq("dispensary_id", dispensary_id)
        .single()
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="dispensary not found")
    dispensary = result.data

    # get their products from SQLite (fast local join)
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT p.mcp_id, p.canonical_title, p.normalized_brand, p.normalized_category,"
            " p.normalized_size, p.price, p.sale_price, p.effective_price,"
            " p.product_url, p.image_url, p.in_stock,"
            " m.normalized_form, m.extract_type, m.hardware_type,"
            " m.cannabinoid_profile, m.dominance_or_type, m.count,"
            " (SELECT COUNT(*) FROM price_comparison_index p2"
            "  WHERE p2.mcp_id = p.mcp_id) AS shop_count,"
            " (SELECT MIN(p3.effective_price) FROM price_comparison_index p3"
            "  WHERE p3.mcp_id = p.mcp_id) AS market_low"
            " FROM price_comparison_index p"
            " JOIN master_canonical_products m ON m.mcp_id = p.mcp_id"
            " WHERE p.dispensary_id = ?"
            " ORDER BY p.normalized_category, p.effective_price ASC",
            (dispensary_id,),
        ).fetchall()

    products = [dict(r) for r in rows]

    # group by category
    by_category: dict[str, list] = {}
    for p in products:
        cat = p.get("normalized_category") or "Other"
        by_category.setdefault(cat, []).append(p)

    return {
        "dispensary": dispensary,
        "product_count": len(products),
        "categories": list(by_category.keys()),
        "products_by_category": by_category,
    }


# ── page routes ───────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/dispensaries")
def dispensaries_page():
    return FileResponse(ROOT / "static" / "dispensaries.html")


@app.get("/dispensary/{dispensary_id}")
def dispensary_page(dispensary_id: str):
    return FileResponse(ROOT / "static" / "dispensary.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
