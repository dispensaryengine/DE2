"""SQLite datastore. Implements the PEK/MCP schema from the PRD, adapted to SQLite."""
import sqlite3
from contextlib import contextmanager

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS dispensaries (
    dispensary_id   TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    platform        TEXT NOT NULL,
    url             TEXT,
    address         TEXT
);

CREATE TABLE IF NOT EXISTS raw_dispensary_product_listings (
    raw_dpl_id      TEXT PRIMARY KEY,
    batch_id        TEXT NOT NULL,
    dispensary_id   TEXT NOT NULL,
    source_platform TEXT,
    source_product_title TEXT,
    source_brand    TEXT,
    source_category TEXT,
    source_subcategory TEXT,
    price           REAL,
    sale_price      REAL,
    thc_raw         TEXT,
    cbd_raw         TEXT,
    weight_raw      TEXT,
    product_url     TEXT,
    image_url       TEXT,
    description     TEXT,
    raw_payload     TEXT,
    quantity        INTEGER,
    in_stock        INTEGER DEFAULT 1,
    scraped_at      TEXT
);

CREATE TABLE IF NOT EXISTS normalized_dispensary_product_listings (
    dpl_id          TEXT PRIMARY KEY,
    raw_dpl_id      TEXT,
    batch_id        TEXT,
    dispensary_id   TEXT NOT NULL,
    source_product_title TEXT,
    normalized_title TEXT,
    source_brand    TEXT,
    normalized_brand TEXT,
    brand_confidence REAL,
    raw_category    TEXT,
    normalized_category TEXT,
    normalized_form TEXT,
    subform         TEXT,
    normalized_product_name TEXT,
    size_value      REAL,
    size_unit       TEXT,
    normalized_size TEXT,
    count           INTEGER,
    package_thc_mg  REAL,
    serving_thc_mg  REAL,
    cannabinoid_profile TEXT,
    ratio           TEXT,
    extract_type    TEXT,
    infusion_type   TEXT,
    hardware_type   TEXT,
    dominance_or_type TEXT,
    thc_value       TEXT,
    price           REAL,
    sale_price      REAL,
    effective_price REAL,
    product_url     TEXT,
    image_url       TEXT,
    comparison_status TEXT,
    proposed_pek    TEXT,
    extraction_confidence REAL
);

CREATE TABLE IF NOT EXISTS brands (
    brand_id        TEXT PRIMARY KEY,
    canonical_brand_name TEXT NOT NULL,
    normalized_brand_key TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS brand_aliases (
    raw_value       TEXT PRIMARY KEY,
    canonical_brand_name TEXT NOT NULL,
    alias_type      TEXT,
    approval_status TEXT
);

CREATE TABLE IF NOT EXISTS master_canonical_products (
    mcp_id          TEXT PRIMARY KEY,
    pek             TEXT UNIQUE NOT NULL,
    canonical_title TEXT NOT NULL,
    search_title    TEXT,
    normalized_brand TEXT,
    normalized_category TEXT,
    normalized_form TEXT,
    subform         TEXT,
    canonical_product_name TEXT,
    normalized_size TEXT,
    count           INTEGER,
    package_thc_mg  REAL,
    cannabinoid_profile TEXT,
    ratio           TEXT,
    extract_type    TEXT,
    infusion_type   TEXT,
    hardware_type   TEXT,
    dominance_or_type TEXT,
    image_url       TEXT,
    review_status   TEXT
);

CREATE TABLE IF NOT EXISTS mcp_dpl_links (
    link_id         TEXT PRIMARY KEY,
    mcp_id          TEXT,
    dpl_id          TEXT,
    match_confidence REAL,
    match_method    TEXT,
    needs_review    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS price_comparison_index (
    price_index_id  TEXT PRIMARY KEY,
    mcp_id          TEXT,
    dpl_id          TEXT,
    dispensary_id   TEXT,
    dispensary_name TEXT,
    canonical_title TEXT,
    normalized_brand TEXT,
    normalized_category TEXT,
    normalized_size TEXT,
    price           REAL,
    sale_price      REAL,
    effective_price REAL,
    product_url     TEXT,
    image_url       TEXT,
    in_stock        INTEGER
);

CREATE TABLE IF NOT EXISTS product_review_queue (
    review_id       TEXT PRIMARY KEY,
    issue_type      TEXT,
    risk_level      TEXT,
    dpl_id          TEXT,
    detail          TEXT,
    status          TEXT DEFAULT 'open'
);

CREATE INDEX IF NOT EXISTS idx_pci_mcp ON price_comparison_index(mcp_id);
CREATE INDEX IF NOT EXISTS idx_pci_search ON price_comparison_index(canonical_title);
CREATE INDEX IF NOT EXISTS idx_mcp_search ON master_canonical_products(search_title);
CREATE INDEX IF NOT EXISTS idx_ndpl_status ON normalized_dispensary_product_listings(comparison_status);
CREATE INDEX IF NOT EXISTS idx_mcp_links_dpl ON mcp_dpl_links(dpl_id);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)


def reset_pipeline_tables():
    """Clear derived tables before a fresh pipeline run (keeps schema)."""
    tables = [
        "dispensaries",
        "raw_dispensary_product_listings",
        "normalized_dispensary_product_listings",
        "master_canonical_products",
        "mcp_dpl_links",
        "price_comparison_index",
        "product_review_queue",
    ]
    with connect() as conn:
        for t in tables:
            conn.execute(f"DELETE FROM {t}")
