"""GoodLife (Flowhub via Heroku proxy) scraper.

GoodLife runs an Angular SPA that calls a Heroku-hosted proxy
(`/flowhub/inventoryByLocation`) protected by an `x-auth-api-key`. The key and
API base live in the site's main JS bundle, so we discover them at runtime
(self-healing if the key rotates) rather than hard-coding a secret. The
per-store `location_id` (a Flowhub UUID) is stored in the registry.
"""
import re
import time

from curl_cffi import requests as cffi_requests

from config import REQUEST_DELAY

SITE = "https://goodlifeweed.com"
DEFAULT_SHOP = SITE + "/location/cannabis-dispensary-buffalo-ny/shop"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_CFG_RE = re.compile(r'apiUrl:"([^"]+)"[^}]*?db_api_key:"([0-9a-f]{32,})"')
_BUNDLE_RE = re.compile(r'main[-.][A-Za-z0-9]+\.js')

_creds_cache = None


def _session():
    s = cffi_requests.Session(impersonate="chrome124")
    s.headers.update({
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": UA,
        "Origin": SITE,
        "Referer": SITE + "/",
    })
    return s


def _credentials(session, shop_url):
    """Discover (api_url, api_key) from the live JS bundle. Cached per process."""
    global _creds_cache
    if _creds_cache:
        return _creds_cache
    try:
        html = session.get(shop_url, timeout=40).text
    except Exception:
        return None, None
    bundle = None
    for src in re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html):
        if _BUNDLE_RE.search(src):
            bundle = src if src.startswith("http") else SITE + "/" + src.lstrip("/")
            break
    if not bundle:
        return None, None
    try:
        js = session.get(bundle, timeout=60).text
    except Exception:
        return None, None
    m = _CFG_RE.search(js)
    if not m:
        return None, None
    _creds_cache = (m.group(1), m.group(2))
    return _creds_cache


def _to_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _map(product):
    potency = product.get("potency") or {}
    thc = potency.get("totalThc") or potency.get("thc") or product.get("thc")
    cbd = potency.get("totalCbd") or potency.get("cbd") or product.get("cbd")
    weight = product.get("weight")
    unit = (product.get("unit") or "").strip()
    weight_raw = None
    if weight not in (None, 0, "0"):
        weight_raw = f"{weight}{unit}" if unit and unit.lower() != "each" else str(weight)

    qty = product.get("quantity")
    in_stock = True
    if qty is not None:
        try:
            qty = int(qty)
            in_stock = qty > 0
        except (TypeError, ValueError):
            qty = None

    return {
        "title": product.get("title"),
        "brand": product.get("brand"),
        "category": product.get("category"),
        "subcategory": None,
        "strain_type": product.get("strainType") or None,
        "thc_raw": str(thc) if thc not in (None, "", 0) else None,
        "cbd_raw": str(cbd) if cbd not in (None, "", 0) else None,
        "weight_raw": weight_raw,
        "price": _to_float(product.get("price")),
        "sale_price": None,
        "quantity": qty,
        "in_stock": in_stock,
        "product_url": None,
        "image_url": product.get("image"),
        "description": product.get("desc") or None,
        "raw_payload": product,
    }


def scrape(dispensary: dict):
    loc = dispensary.get("goodlife_location_id")
    if not loc:
        return []
    shop_url = dispensary.get("url") or DEFAULT_SHOP
    session = _session()
    api_url, api_key = _credentials(session, shop_url)
    if not api_url or not api_key:
        return []

    headers = {"x-auth-api-key": api_key, "Content-Type": "application/json"}
    url = api_url.rstrip("/") + "/flowhub/inventoryByLocation"
    params = {"location_id": loc, "toggleVape": "false"}

    for attempt in range(5):
        try:
            resp = session.get(url, params=params, headers=headers, timeout=60)
            if resp.status_code == 200:
                data = resp.json()
                items = data if isinstance(data, list) else (
                    data.get("data") or data.get("products") or [])
                if items:
                    return [_map(p) for p in items if isinstance(p, dict)]
        except Exception:
            pass
        time.sleep(1 + attempt * 2)
    return []
