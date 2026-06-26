"""KushMart custom SSR scraper.

KushMart renders its shop server-side with ?page=N pagination. We pull each
page until one returns zero product cards, parsing the listing markup with
BeautifulSoup.
"""
import re
import time
from urllib.parse import urlparse

from bs4 import BeautifulSoup

try:
    from curl_cffi import requests as cffi_requests
except Exception:  # pragma: no cover
    cffi_requests = None

try:
    import requests as plain_requests
except Exception:  # pragma: no cover
    plain_requests = None

from config import REQUEST_DELAY

BASE = "https://kushmart.com"
MAX_PAGES = 100

STRAIN_RE = re.compile(r"(indica|sativa|hybrid|50/50)", re.I)
PACK_RE = re.compile(r"(\d+(?:\.\d+)?\s*g\b|\d+\s*pk|\d+\s*pack)", re.I)
PRICE_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)")

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _session():
    if cffi_requests is not None:
        try:
            s = cffi_requests.Session(impersonate="chrome124")
            s.headers.update(HEADERS)
            return s, True
        except Exception:
            pass
    if plain_requests is not None:
        s = plain_requests.Session()
        s.headers.update(HEADERS)
        return s, False
    raise RuntimeError("No HTTP client available (curl_cffi/requests both missing)")


def _get(session, url):
    for attempt in range(3):
        try:
            resp = session.get(url, timeout=60)
            if resp.status_code == 200:
                return resp.text
        except Exception:
            pass
        time.sleep(1 + attempt)
    return None


def _meta_parts(card):
    for s in card.stripped_strings:
        if "·" in s:
            return [p.strip() for p in s.split("·") if p.strip()]
    return []


def _price(card):
    for text in card.find_all(string=lambda t: t and "$" in t):
        m = PRICE_RE.search(text)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                return None
    return None


def _parse_card(card, category=None):
    href = card.get("href") or ""
    path = urlparse(href).path
    if len(path.split("/")) != 6:
        return None

    strong = card.find("strong")
    name = strong.get_text(strip=True) if strong else None

    parts = _meta_parts(card)
    brand = parts[0] if parts else None
    strain_type = thc = cbd = pack = None
    for part in parts:
        low = part.lower()
        if strain_type is None and STRAIN_RE.search(part):
            strain_type = part
        elif low.startswith("thc"):
            thc = part
        elif low.startswith("cbd"):
            cbd = part
        elif pack is None and ("gram" in low or PACK_RE.search(part)):
            pack = part

    price = _price(card)

    payload = {
        "href": href,
        "name": name,
        "brand": brand,
        "strain_type": strain_type,
        "thc": thc,
        "cbd": cbd,
        "pack": pack,
        "price": price,
        "meta_parts": parts,
    }

    return {
        "title": name,
        "brand": brand,
        "category": category,
        "subcategory": None,
        "strain_type": strain_type,
        "thc_raw": thc,
        "cbd_raw": cbd,
        "weight_raw": pack,
        "price": price,
        "sale_price": None,
        "product_url": BASE + href if href.startswith("/") else href,
        "image_url": None,
        "description": None,
        "raw_payload": payload,
    }


def _parse_page(html, category=None):
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("a[data-ssr-card]")
    rows = []
    for card in cards:
        row = _parse_card(card, category)
        if row:
            rows.append(row)
    return rows


def _categories(html, shop_url):
    """Return [(category_url, category_name)] from the shop landing page."""
    soup = BeautifulSoup(html, "html.parser")
    shop_path = urlparse(shop_url).path.rstrip("/")
    cats = []
    for a in soup.select("a[data-ssr-card]"):
        href = a.get("href") or ""
        path = urlparse(href).path.rstrip("/")
        # category nav card: <shop>/<category> (exactly one extra segment)
        if path.startswith(shop_path + "/") and path.count("/") == shop_path.count("/") + 1:
            name = a.get_text(strip=True) or None
            cats.append((BASE + path if path.startswith("/") else path, name))
    return cats


def scrape(dispensary: dict):
    shop_url = dispensary.get("kushmart_url")
    if not shop_url:
        return []
    session, _ = _session()

    landing = _get(session, shop_url)
    if not landing:
        return []
    categories = _categories(landing, shop_url)

    out, seen = [], set()
    for cat_url, cat_name in categories:
        for page in range(1, MAX_PAGES + 1):
            url = cat_url if page == 1 else f"{cat_url}?page={page}"
            html = _get(session, url)
            if not html:
                break
            rows = _parse_page(html, cat_name)
            if not rows:
                break
            new = 0
            for row in rows:
                key = row["product_url"]
                if key in seen:
                    continue
                seen.add(key)
                out.append(row)
                new += 1
            if new == 0:
                break
            time.sleep(REQUEST_DELAY)
    return out
