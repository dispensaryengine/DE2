"""Blaze / Tymber JSON:API scraper.

Queries the hosted Blaze ecom API for a store and emits one row per
unit-price tier so size-level price comparison works. Money values arrive
in cents and are converted to dollars.
"""
import time

from curl_cffi import requests as cffi_requests

from config import REQUEST_DELAY

API_URL = "https://ecom-api.blaze.me/api/v1/products/"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _session(origin):
    s = cffi_requests.Session(impersonate="chrome124")
    s.headers.update({
        "Accept": "application/vnd.api+json",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": UA,
        "X-App-Mode": "default",
    })
    return s


def _money(value):
    """Return dollars (float) from a Blaze money dict with cents `amount`."""
    if not isinstance(value, dict):
        return None
    amount = value.get("amount")
    if amount is None:
        return None
    try:
        return float(amount) / 100.0
    except (TypeError, ValueError):
        return None


def _fetch_all(session, store_id, origin):
    headers = {
        "Origin": origin.rstrip("/"),
        "Referer": origin.rstrip("/") + "/",
        "X-Store": store_id,
    }
    products, offset = [], 0
    while True:
        params = {"limit": 100, "offset": offset, "delivery_type": "pickup"}
        page = []
        for attempt in range(3):
            try:
                resp = session.get(API_URL, params=params, headers=headers, timeout=60)
                if resp.status_code == 200:
                    payload = resp.json()
                    page = payload.get("data") or []
                    total = (payload.get("meta") or {}).get("total_count")
                    break
            except Exception:
                pass
            time.sleep(1 + attempt)
        else:
            break

        if not page:
            break
        products.extend(page)
        offset += len(page)
        if total is not None and offset >= total:
            break
        time.sleep(REQUEST_DELAY)
    return products


def _expand(raw, store_url):
    attrs = raw.get("attributes") or {}
    potency = attrs.get("potency") or {}
    size = attrs.get("size") or {}

    pos_inv = attrs.get("pos_inventory") or {}
    quantity = pos_inv.get("quantity") if isinstance(pos_inv, dict) else None
    in_stock = bool(attrs.get("in_stock", True))
    if quantity is not None and quantity <= 0:
        in_stock = False

    base = {
        "title": attrs.get("name"),
        "brand": attrs.get("brand"),
        "category": attrs.get("type") or attrs.get("product_type"),
        "subcategory": None,
        "strain_type": attrs.get("strain") or attrs.get("flower_type"),
        "thc_raw": str(potency.get("thc")) if potency.get("thc") is not None else None,
        "cbd_raw": str(potency.get("cbd")) if potency.get("cbd") is not None else None,
        "image_url": attrs.get("main_image"),
        "product_url": store_url,
        "description": attrs.get("description"),
        "quantity": quantity,
        "in_stock": in_stock,
        "raw_payload": attrs,
    }

    unit_prices = attrs.get("unit_prices") or []
    if not unit_prices:
        row = dict(base)
        row["weight_raw"] = size.get("display_text")
        row["price"] = _money(attrs.get("unit_price"))
        row["sale_price"] = _money(attrs.get("discount_price"))
        yield row
        return

    for tier in unit_prices:
        row = dict(base)
        row["weight_raw"] = tier.get("display_name") or size.get("display_text")
        row["price"] = _money(tier.get("price"))
        row["sale_price"] = _money(tier.get("discount_price"))
        yield row


def scrape(dispensary: dict):
    store_id = dispensary.get("blaze_store_id")
    store_url = dispensary.get("url") or ""
    session = _session(store_url)
    products = _fetch_all(session, store_id, store_url)
    out = []
    for raw in products:
        out.extend(_expand(raw, store_url))
    return out
