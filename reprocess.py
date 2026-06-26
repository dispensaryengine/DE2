"""Re-run normalize + index on already-scraped raw data (no re-scrape)."""
from engine import db
from engine import pipeline

DERIVED = [
    "normalized_dispensary_product_listings",
    "master_canonical_products",
    "mcp_dpl_links",
    "price_comparison_index",
    "product_review_queue",
]


def main():
    db.init_db()
    with db.connect() as conn:
        for t in DERIVED:
            conn.execute(f"DELETE FROM {t}")
    print("[2/3] Normalizing ...")
    pipeline.normalize_all()
    print("[3/3] Matching + price index ...")
    stats = pipeline.build_mcps_and_index()
    print("Done:", stats)


if __name__ == "__main__":
    main()
