"""Verification report: confirms the engine answers the two core questions."""
from engine import db


def line(label, val):
    print(f"  {label:<34} {val}")


with db.connect() as c:
    print("\n=== PIPELINE VERIFICATION REPORT ===\n")
    line("Dispensaries", c.execute("SELECT COUNT(*) FROM dispensaries").fetchone()[0])
    line("Raw listings", c.execute("SELECT COUNT(*) FROM raw_dispensary_product_listings").fetchone()[0])
    line("Cannabis-eligible (normalized)",
         c.execute("SELECT COUNT(*) FROM normalized_dispensary_product_listings "
                   "WHERE comparison_status='eligible_cannabis'").fetchone()[0])
    line("Excluded accessories/merch",
         c.execute("SELECT COUNT(*) FROM normalized_dispensary_product_listings "
                   "WHERE comparison_status LIKE 'excluded%'").fetchone()[0])
    line("Canonical products (MCPs)",
         c.execute("SELECT COUNT(*) FROM master_canonical_products").fetchone()[0])
    line("Priced listings (index)",
         c.execute("SELECT COUNT(*) FROM price_comparison_index").fetchone()[0])
    line("Products at 2+ dispensaries",
         c.execute("SELECT COUNT(*) FROM (SELECT mcp_id FROM price_comparison_index "
                   "GROUP BY mcp_id HAVING COUNT(*)>1)").fetchone()[0])

    print("\n--- Q1: Find a product + availability (sample) ---")
    for r in c.execute(
        "SELECT m.canonical_title, COUNT(*) n FROM master_canonical_products m "
        "JOIN price_comparison_index p ON p.mcp_id=m.mcp_id "
        "GROUP BY m.mcp_id HAVING n>=5 ORDER BY n DESC LIMIT 5"):
        print(f"  '{r[0]}' -> available at {r[1]} dispensaries")

    print("\n--- Q2: Side-by-side price comparison (best multi-shop example) ---")
    mid = c.execute(
        "SELECT mcp_id FROM price_comparison_index GROUP BY mcp_id "
        "HAVING COUNT(DISTINCT effective_price)>=4 ORDER BY COUNT(*) DESC LIMIT 1"
    ).fetchone()[0]
    title = c.execute("SELECT canonical_title FROM master_canonical_products "
                      "WHERE mcp_id=?", (mid,)).fetchone()[0]
    print(f"  Product: {title}\n")
    offers = c.execute(
        "SELECT dispensary_name, price, sale_price, effective_price "
        "FROM price_comparison_index WHERE mcp_id=? ORDER BY effective_price", (mid,)
    ).fetchall()
    for i, o in enumerate(offers):
        tag = "  <- BEST VALUE" if i == 0 else ""
        was = f" (was {o[1]:.2f})" if o[2] and o[1] and o[2] < o[1] else ""
        print(f"    #{i+1}  {o[3]:>7.2f} USD{was}  {o[0]}{tag}")
    lo, hi = offers[0][3], offers[-1][3]
    print(f"\n  Spread: {lo:.2f} - {hi:.2f} USD  (save {hi-lo:.2f}, {(hi-lo)/hi*100:.0f}%)")
    print()
