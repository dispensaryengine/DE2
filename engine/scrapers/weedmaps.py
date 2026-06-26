"""Weedmaps public discovery REST scraper.

Paginates a dispensary's menu items and expands each item into one row per
price tier (Weedmaps keys prices by unit, e.g. gram / eighth_ounce).
"""
import math
import time

from curl_cffi import requests as cffi_requests

from config import REQUEST_DELAY

API_BASE = "https://api-g.weedmaps.com"
PAGE_SIZE = 100

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _session(store):
    s = cffi_requests.Session(impersonate="chrome124")
    s.headers.update({
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": store,
        "Referer": store + "/",
        "User-Agent": UA,
    })
    return s


def _to_float(v):
    if v is None:
        return None
    try:
        return float(str(v).replace("$", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _get(session, slug, page):
    url = (
        f"{API_BASE}/discovery/v1/listings/dispensaries/{slug}/menu_items"
        "?include[]=facets.categories"
    )
    params = {"page": page, "page_size": PAGE_SIZE}
    for attempt in range(3):
        try:
            resp = session.get(url, params=params, timeout=30)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        time.sleep(REQUEST_DELAY * (attempt + 1))
    return None


def _expand(item):
    base = {
        "title": item.get("name"),
        "brand": (item.get("brand_endorsement") or {}).get("brand_name"),
        "category": (item.get("category") or {}).get("name"),
        "subcategory": (item.get("edge_category") or {}).get("name"),
        "strain_type": (item.get("genetics_tag") or {}).get("name"),
        "thc_raw": None,
        "cbd_raw": None,
        "image_url": (item.get("avatar_image") or {}).get("original_url"),
        "description": item.get("body") or None,
        "product_url": None,
    }

    prices = item.get("prices")
    if isinstance(prices, dict) and prices:
        for unit, tier in prices.items():
            if unit == "grams_per_eighth" or not isinstance(tier, dict):
                continue
            cur = _to_float(tier.get("price"))
            orig = _to_float(tier.get("original_price"))
            on_sale = tier.get("on_sale")
            row = dict(base)
            row["weight_raw"] = tier.get("label") or unit
            if on_sale and orig is not None and cur is not None and orig > cur:
                row["price"] = orig
                row["sale_price"] = cur
            else:
                row["price"] = cur
                row["sale_price"] = None
            row["raw_payload"] = {
                **item,
                "_tier": {
                    "unit": unit,
                    "label": tier.get("label"),
                    "price": tier.get("price"),
                    "original_price": tier.get("original_price"),
                    "on_sale": tier.get("on_sale"),
                },
            }
            yield row
        return

    row = dict(base)
    row["weight_raw"] = None
    row["price"] = _to_float(item.get("price"))
    row["sale_price"] = None
    row["raw_payload"] = item
    yield row


def scrape(dispensary: dict):
    """Scrape a Weedmaps dispensary menu. `dispensary` is a registry record."""
    slug = dispensary.get("weedmaps_slug") or dispensary.get("external_id")
    if not slug:
        return []
    store = (dispensary.get("url") or f"https://{slug}.wm.store").rstrip("/")
    session = _session(store)

    out = []
    page = 1
    total_pages = 1
    while page <= total_pages:
        data = _get(session, slug, page)
        if not data:
            break
        if page == 1:
            total = (data.get("meta") or {}).get("total_menu_items") or 0
            total_pages = max(1, math.ceil(total / PAGE_SIZE)) if total else 1
        items = ((data.get("data") or {}).get("menu_items")) or []
        if not items:
            break
        for item in items:
            out.extend(_expand(item))
        page += 1
        time.sleep(REQUEST_DELAY)
    return out
