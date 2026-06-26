"""Dispense (AIQ) REST scraper.

Walks every product category for a venue and paginates each category's
products. Products are deduped by id across categories. One row per product
(Dispense exposes a single price/weight per product record).
"""
import re
import time

from curl_cffi import requests as cffi_requests

from config import REQUEST_DELAY

API_BASE = "https://api.dispenseapp.com"
MENU_BASE = "https://menus.dispenseapp.com"
API_KEY = "49dac8e0-7743-11e9-8e3f-a5601eb2e936"
PAGE_SIZE = 200

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _session():
    s = cffi_requests.Session(impersonate="chrome124")
    s.headers.update({
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": MENU_BASE,
        "Referer": f"{MENU_BASE}/",
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


def _get(session, venue, path, params):
    url = API_BASE + path
    headers = {
        "api-key": API_KEY,
        "x-pathname": path,
        "x-url": f"{MENU_BASE}/{venue}/menu",
    }
    for attempt in range(3):
        try:
            resp = session.get(url, params=params, headers=headers, timeout=30)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        time.sleep(REQUEST_DELAY * (attempt + 1))
    return None


def _data_list(payload):
    if isinstance(payload, dict):
        return payload.get("data") or []
    if isinstance(payload, list):
        return payload
    return []


def _labs(product):
    labs = product.get("labs") or {}
    thc = labs.get("thc")
    cbd = labs.get("cbd")
    if thc is None:
        pos = product.get("posLastSyncData")
        if pos is not None:
            m = re.search(r"thc[\"']?\s*[:=]\s*[\"']?\s*([\d.]+)", str(pos), re.IGNORECASE)
            if m:
                thc = m.group(1)
    return thc, cbd


def _row(product):
    brand = product.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name") or brand.get("brandName")

    thc, cbd = _labs(product)

    image_url = None
    for im in product.get("images") or []:
        if isinstance(im, dict) and im.get("fileUrl"):
            image_url = im["fileUrl"]
            break

    price = _to_float(product.get("price"))
    disc = _to_float(product.get("priceWithDiscounts"))
    sale_price = disc if (disc is not None and price is not None and disc < price) else None

    qty = product.get("quantity") or product.get("remainingQuantity")
    try:
        qty = int(qty) if qty is not None else None
    except (TypeError, ValueError):
        qty = None
    in_stock = product.get("inStock", True)
    if qty is not None and qty <= 0:
        in_stock = False

    return {
        "title": product.get("name"),
        "brand": brand,
        "category": product.get("productCategoryName"),
        "subcategory": product.get("subType"),
        "strain_type": product.get("cannabisType"),
        "thc_raw": str(thc) if thc is not None else None,
        "cbd_raw": str(cbd) if cbd is not None else None,
        "weight_raw": product.get("weightFormatted") or product.get("weight"),
        "price": price,
        "sale_price": sale_price,
        "quantity": qty,
        "in_stock": bool(in_stock),
        "product_url": product.get("productUrl"),
        "image_url": image_url,
        "description": product.get("description"),
        "raw_payload": product,
    }


def scrape(dispensary: dict):
    """Scrape a Dispense venue. `dispensary` is a registry record."""
    venue = dispensary.get("dispense_venue_id")
    if not venue:
        return []

    session = _session()
    cats = _get(
        session, venue,
        f"/v1/venues/{venue}/product-categories",
        {"orderPickUpType": "IN_STORE"},
    )
    categories = _data_list(cats)
    if not categories:
        return []

    seen, out = set(), []
    for cat in categories:
        cat_id = cat.get("id") or cat.get("_id")
        if not cat_id:
            continue
        skip = 0
        while True:
            time.sleep(REQUEST_DELAY)
            data = _get(
                session, venue,
                f"/v1/venues/{venue}/product-categories/{cat_id}/products",
                {"orderPickUpType": "IN_STORE", "limit": PAGE_SIZE, "skip": skip},
            )
            batch = _data_list(data)
            if not batch:
                break
            for p in batch:
                pid = p.get("id") or p.get("_id")
                if pid and pid in seen:
                    continue
                if pid:
                    seen.add(pid)
                out.append(_row(p))
            if len(batch) < PAGE_SIZE:
                break
            skip += PAGE_SIZE
    return out
