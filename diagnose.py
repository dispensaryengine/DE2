"""Diagnostics on the built index: per-platform coverage + match quality."""
import sqlite3
from collections import Counter, defaultdict

from config import DB_PATH


def main():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row

    print("== Per-platform funnel ==")
    rows = c.execute("""
        SELECT d.platform,
               COUNT(*) AS total,
               SUM(CASE WHEN n.comparison_status='eligible_cannabis' THEN 1 ELSE 0 END) AS eligible,
               SUM(CASE WHEN n.proposed_pek IS NOT NULL AND n.proposed_pek!='' THEN 1 ELSE 0 END) AS with_pek,
               SUM(CASE WHEN n.normalized_brand IS NOT NULL AND n.normalized_brand!='' THEN 1 ELSE 0 END) AS with_brand
        FROM normalized_dispensary_product_listings n
        JOIN dispensaries d ON d.dispensary_id=n.dispensary_id
        GROUP BY d.platform ORDER BY total DESC
    """).fetchall()
    print(f"{'platform':12} {'total':>7} {'eligible':>9} {'with_pek':>9} {'with_brand':>11}")
    for r in rows:
        print(f"{r['platform']:12} {r['total']:>7} {r['eligible']:>9} {r['with_pek']:>9} {r['with_brand']:>11}")

    print("\n== Index: products by #dispensaries ==")
    idx = c.execute("SELECT mcp_id, dispensary_id, dispensary_name FROM price_comparison_index").fetchall()
    shops = defaultdict(set)
    for r in idx:
        shops[r["mcp_id"]].add(r["dispensary_id"])
    dist = Counter(len(s) for s in shops.values())
    total_products = len(shops)
    multi = sum(1 for s in shops.values() if len(s) > 1)
    print(f"total priced products: {total_products}, multi-shop (2+): {multi}")
    for k in sorted(dist)[:10]:
        print(f"  at {k} dispensaries: {dist[k]} products")

    print("\n== Top multi-shop products (most dispensaries) ==")
    mcp_titles = {r["mcp_id"]: r["canonical_title"]
                  for r in c.execute("SELECT mcp_id, canonical_title FROM master_canonical_products")}
    top = sorted(shops.items(), key=lambda kv: len(kv[1]), reverse=True)[:12]
    for mcp_id, s in top:
        print(f"  [{len(s):2} shops] {mcp_titles.get(mcp_id)}")

    print("\n== Brand-less PEK merge risk (empty brand, 2+ shops) ==")
    # find MCPs with empty brand that span multiple shops
    empty_brand = c.execute("""
        SELECT mcp_id, canonical_title FROM master_canonical_products
        WHERE normalized_brand IS NULL OR normalized_brand=''
    """).fetchall()
    eb_ids = {r["mcp_id"]: r["canonical_title"] for r in empty_brand}
    risky = [(mid, len(shops.get(mid, set()))) for mid in eb_ids if len(shops.get(mid, set())) > 1]
    print(f"empty-brand multi-shop products: {len(risky)}")
    for mid, n in sorted(risky, key=lambda x: -x[1])[:10]:
        print(f"  [{n} shops] {eb_ids[mid]}")


if __name__ == "__main__":
    main()
