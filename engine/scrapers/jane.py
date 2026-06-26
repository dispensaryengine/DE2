"""iHeartJane (Algolia) scraper.

Reads the Algolia app id / api key out of the store HTML, then queries the
hosted Algolia menu index. Each priced weight tier is emitted as its own row
so size-level price comparison works.
"""
import re
import time

from curl_cffi import requests as cffi_requests

from config import REQUEST_DELAY

SEARCH_URL = "https://search.iheartjane.com/1/indexes/menu-products-production/query"
INDEX_NAME = "menu-products-production"

FALLBACK_APP_ID = "VFM4X0N23A"
FALLBACK_API_KEY = "edc5435c65d771cecbd98bbd488aa8d3"

APP_ID_RE = re.compile(r'algoliaAppId["\s:]+["\']([A-Z0-9]{8,})["\']')
API_KEY_RE = re.compile(r'algoliaApiKey["\s:]+["\']([a-f0-9]{30,})["\']')

WEIGHTS = [
    "gram", "half_gram", "two_gram", "eighth_ounce",
    "quarter_ounce", "half_ounce", "ounce", "each",
]

# Jane's unit tokens -> a gram string the normalizer can parse reliably.
WEIGHT_LABELS = {
    "gram": "1g",
    "half_gram": "0.5g",
    "two_gram": "2g",
    "eighth_ounce": "3.5g",
    "quarter_ounce": "7g",
    "half_ounce": "14g",
    "ounce": "28g",
    "each": "each",
}

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _session():
    s = cffi_requests.Session(impersonate="chrome124")
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    return s


def _credentials(session, store_url):
    try:
        resp = session.get(store_url, timeout=30)
        if resp.status_code == 200:
            html = resp.text
            app = APP_ID_RE.search(html)
            key = API_KEY_RE.search(html)
            if app and key:
                return app.group(1), key.group(1)
    except Exception:
        pass
    return FALLBACK_APP_ID, FALLBACK_API_KEY


def _query_page(session, app_id, api_key, store_id, page):
    headers = {
        "X-Algolia-Application-Id": app_id,
        "X-Algolia-API-Key": api_key,
        "Content-Type": "application/json",
        "Referer": "https://www.iheartjane.com/",
        "Origin": "https://www.iheartjane.com",
    }
    body = {
        "query": "",
        "filters": f"store_id:{store_id}",
        "hitsPerPage": 1000,
        "page": page,
    }
    for attempt in range(3):
        try:
            resp = session.post(SEARCH_URL, json=body, headers=headers, timeout=60)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        time.sleep(1 + attempt)
    return {}


def _image_url(hit):
    for photo in hit.get("product_photos") or []:
        if isinstance(photo, dict):
            urls = photo.get("urls") or {}
            original = urls.get("original")
            if original:
                return original
    image_urls = hit.get("image_urls") or []
    if image_urls:
        return image_urls[0]
    return None


def _expand(hit):
    image_url = _image_url(hit)
    base = {
        "title": hit.get("name"),
        "brand": hit.get("brand"),
        "category": hit.get("kind"),
        "subcategory": hit.get("kind_subtype"),
        "strain_type": hit.get("strain"),
        "thc_raw": str(hit.get("percent_thc")) if hit.get("percent_thc") is not None else None,
        "cbd_raw": str(hit.get("percent_cbd")) if hit.get("percent_cbd") is not None else None,
        "image_url": image_url,
        "product_url": None,
        "description": hit.get("description"),
        "raw_payload": hit,
    }

    for w in WEIGHTS:
        price = hit.get(f"price_{w}")
        if price is None:
            continue
        sale = None
        for sale_key in (f"discounted_price_{w}", f"special_price_{w}"):
            candidate = hit.get(sale_key)
            if candidate is not None and candidate < price:
                sale = candidate
                break
        row = dict(base)
        row["weight_raw"] = WEIGHT_LABELS.get(w, w)
        row["price"] = price
        row["sale_price"] = sale
        yield row


def scrape(dispensary: dict):
    store_id = dispensary.get("external_id")
    store_url = dispensary.get("url") or "https://www.iheartjane.com/"
    session = _session()
    app_id, api_key = _credentials(session, store_url)

    out = []
    data = _query_page(session, app_id, api_key, store_id, 0)
    hits = data.get("hits") or []
    for hit in hits:
        out.extend(_expand(hit))

    nb_pages = data.get("nbPages") or 1
    for page in range(1, nb_pages):
        time.sleep(REQUEST_DELAY)
        data = _query_page(session, app_id, api_key, store_id, page)
        for hit in data.get("hits") or []:
            out.extend(_expand(hit))

    return out
