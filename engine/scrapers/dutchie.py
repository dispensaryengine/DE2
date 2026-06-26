"""Dutchie GraphQL scraper (persisted queries). Adapted from the supplied DUTCHIE scraper.

Each weight option is emitted as a separate raw product so size-level price
comparison works (a 1g and a 3.5g of the same flower are different products).
"""
import json
import time
import urllib.parse

from curl_cffi import requests as cffi_requests

from config import REQUEST_DELAY

GRAPHQL_URL = "https://dutchie.com/graphql"
HASH_FILTERED_PRODUCTS = "98b4aaef79a84ae804b64d550f98dd64d7ba0aa6d836eb6b5d4b2ae815c95e32"

HEADERS = {
    "accept": "application/json",
    "accept-language": "en-US,en;q=0.9",
    "apollo-require-preflight": "true",
    "content-type": "application/json",
    "origin": "https://dutchie.com",
    "referer": "https://dutchie.com/",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


def _session():
    s = cffi_requests.Session(impersonate="chrome124")
    s.headers.update(HEADERS)
    try:
        s.get("https://dutchie.com/", timeout=15)
    except Exception:
        pass
    return s


def _graphql_get(session, operation, variables, sha256):
    params = {
        "operationName": operation,
        "variables": json.dumps(variables, separators=(",", ":")),
        "extensions": json.dumps(
            {"persistedQuery": {"version": 1, "sha256Hash": sha256}},
            separators=(",", ":"),
        ),
    }
    url = GRAPHQL_URL + "?" + urllib.parse.urlencode(params)
    for attempt in range(3):
        resp = session.get(url, timeout=60)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 403 and attempt < 2:
            time.sleep(1 + attempt)
            try:
                session.get("https://dutchie.com/", timeout=10)
            except Exception:
                pass
            continue
        resp.raise_for_status()
    return {}


def _fetch_all(session, dispensary_id, pricing_type="rec"):
    products, page, total_pages = [], 0, None
    while True:
        variables = {
            "includeEnterpriseSpecials": False,
            "productsFilter": {
                "dispensaryId": dispensary_id,
                "pricingType": pricing_type,
                "strainTypes": [],
                "subcategories": [],
                "Status": "Active",
                "types": [],
                "useCache": True,
                "isDefaultSort": True,
                "sortBy": "popularSortIdx",
                "sortDirection": 1,
                "bypassOnlineThresholds": False,
                "isKioskMenu": False,
                "removeProductsBelowOptionThresholds": True,
                "platformType": "ONLINE_MENU",
                "preOrderType": None,
            },
            "page": page,
            "perPage": 100,
        }
        data = _graphql_get(session, "FilteredProducts", variables, HASH_FILTERED_PRODUCTS)
        fp = (data.get("data") or {}).get("filteredProducts") or {}
        batch = fp.get("products") or []
        if total_pages is None:
            total_pages = (fp.get("queryInfo") or {}).get("totalPages", 1) or 1
        products.extend(batch)
        page += 1
        if page >= total_pages or not batch:
            break
        time.sleep(REQUEST_DELAY)
    return products


def _expand(p):
    """Yield one raw product dict per weight option."""
    thc = p.get("THCContent") or {}
    thc_range = thc.get("range") or []
    thc_val = None
    if thc_range:
        thc_val = thc_range[-1]
    options = p.get("Options") or []
    rec = p.get("recPrices") or []
    rec_special = p.get("recSpecialPrices") or []

    base = {
        "product_id": p.get("_id"),
        "title": p.get("Name"),
        "brand": p.get("brandName"),
        "category": p.get("type"),
        "subcategory": p.get("subcategory"),
        "strain_type": p.get("strainType"),
        "thc_raw": str(thc_val) if thc_val is not None else None,
        "cbd_raw": None,
        "image_url": p.get("Image"),
        "description": (p.get("description") or "")[:500] or None,
        "product_url": None,
        "raw_payload": p,
    }

    if not options:
        row = dict(base)
        row["weight_raw"] = None
        row["price"] = rec[0] if rec else None
        row["sale_price"] = rec_special[0] if rec_special else None
        yield row
        return

    for i, opt in enumerate(options):
        row = dict(base)
        row["weight_raw"] = opt
        row["price"] = rec[i] if i < len(rec) else None
        sp = rec_special[i] if i < len(rec_special) else None
        # Dutchie uses 0 to mean "no special"
        row["sale_price"] = sp if sp else None
        yield row


def scrape(dispensary: dict):
    """Scrape a Dutchie dispensary. `dispensary` is a registry record."""
    session = _session()
    raw_products = _fetch_all(session, dispensary["external_id"])
    out = []
    for p in raw_products:
        out.extend(_expand(p))
    return out
