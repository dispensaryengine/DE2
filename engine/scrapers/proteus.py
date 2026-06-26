"""Proteus420 HTML/AJAX storefront scraper.

Proteus serves a ColdFusion (.cfm) cart: the cart page exposes category
anchors, and an ajax endpoint returns rendered product cards per category.
Each card becomes one raw product row (weight is parsed from the title when
present, since Proteus does not expose structured per-size pricing on a card).
"""
import re
import time
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Comment
from curl_cffi import requests as cffi_requests

from config import REQUEST_DELAY

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

SKIP_CATS = {"featured", "onsale", "onsalepage"}
INV_RE = re.compile(r"Inv:\s*(\d+)", re.IGNORECASE)
WEIGHT_RE = re.compile(r"(\d+(?:\.\d+)?|\.\d+)\s*(mg|g|gram(?:s)?|oz|ounce|ml)\b", re.IGNORECASE)


def _session():
    s = cffi_requests.Session(impersonate="chrome124")
    s.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s


def _price(text):
    if not text:
        return None
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(text).replace("$", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _resolve(dispensary):
    """Return (base, cart_page, products_url, acc, loc) for the given record."""
    ptype = dispensary.get("proteus_type")
    host = (dispensary.get("proteus_host") or "").rstrip("/")
    external_id = dispensary.get("external_id") or ""
    parsed = urlparse(host)
    root = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else host

    acc = loc = None
    if ":" in external_id:
        acc, loc = external_id.split(":", 1)

    if ptype == "standalone":
        base = root
        return base, base + "/cart/", base + "/cart/cart/ajax_getproducts.cfm", None, None
    if ptype == "hosted":
        base = host
        return base, base + "/", base + "/cart/ajax_getproducts.cfm", acc, loc
    if ptype == "cloud2":
        # Best-effort: Proteus "cloud2" sites are not documented; try a
        # hosted-style cfm flow against cart.<id>.com.
        base = f"https://cart.{dispensary.get('id', '')}.com"
        return base, base + "/", base + "/cart/ajax_getproducts.cfm", acc, loc
    return None, None, None, None, None


def _categories(soup):
    cats = []
    for a in soup.select("a.getproducts[data-id][data-catname]"):
        cat_id = a.get("data-id")
        catname = (a.get("data-catname") or "").strip()
        if not cat_id or not catname:
            continue
        if catname.lower() in SKIP_CATS:
            continue
        cats.append((cat_id, catname))
    return cats


def _lab_values(card):
    thc_raw = cbd_raw = None
    lab = card.select_one(".labinfo")
    if not lab:
        return thc_raw, cbd_raw
    for span in lab.find_all("span"):
        txt = span.get_text(" ", strip=True)
        up = txt.upper()
        if "THC" in up and thc_raw is None:
            thc_raw = txt
        elif "CBD" in up and cbd_raw is None:
            cbd_raw = txt
    return thc_raw, cbd_raw


def _inventory(card):
    for comment in card.find_all(string=lambda t: isinstance(t, Comment)):
        m = INV_RE.search(comment)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return None
    return None


def _parse_cards(html, base, category_name):
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for card in soup.select(".product-card-wrapper"):
        data_id = card.get("data-id")
        link = card.select_one("a.item_view[data-id]")
        title = brand = prodname = href = None
        if link:
            title = link.get("title") or link.get_text(strip=True) or None
            brand = link.get("data-brandname")
            prodname = link.get("data-prodname")
            href = link.get("href")

        span_el = card.select_one(".price span")
        span_price = _price(span_el.get_text() if span_el else None)
        sale_el = card.select_one(".product_sale_price")
        sale_price = _price(sale_el.get_text() if sale_el else None)
        reg_el = card.select_one(".product_regular_price")
        regular_price = _price(reg_el.get_text() if reg_el else None)

        price = regular_price if regular_price is not None else span_price
        if price is None:
            price = sale_price
        final_sale = (
            sale_price
            if (sale_price is not None and price is not None and sale_price < price)
            else None
        )

        img_el = card.select_one("img.product-image")
        image_url = None
        if img_el:
            image_url = img_el.get("data-src") or img_el.get("src")
            if image_url:
                image_url = urljoin(base + "/", image_url)

        thc_raw, cbd_raw = _lab_values(card)
        inventory = _inventory(card)

        weight_raw = None
        if title:
            wm = WEIGHT_RE.search(title)
            if wm:
                weight_raw = wm.group(0)

        product_url = urljoin(base + "/", href) if href else None

        payload = {
            "data_id": data_id,
            "title": title,
            "brand": brand,
            "prodname": prodname,
            "href": href,
            "category": category_name,
            "span_price": span_price,
            "sale_price": sale_price,
            "regular_price": regular_price,
            "image_url": image_url,
            "inventory": inventory,
            "thc_raw": thc_raw,
            "cbd_raw": cbd_raw,
        }

        rows.append({
            "title": title,
            "brand": brand,
            "category": category_name,
            "subcategory": None,
            "strain_type": None,
            "thc_raw": thc_raw,
            "cbd_raw": cbd_raw,
            "weight_raw": weight_raw,
            "price": price,
            "sale_price": final_sale,
            "product_url": product_url,
            "image_url": image_url,
            "description": None,
            "raw_payload": payload,
        })
    return rows


def scrape(dispensary: dict):
    """Scrape a Proteus420 dispensary. `dispensary` is a registry record."""
    base, cart_page, products_url, acc, loc = _resolve(dispensary)
    if not base:
        return []

    session = _session()
    try:
        if acc and loc:
            session.cookies.set("acc", acc)
            session.cookies.set("loc", loc)
            session.cookies.set("shoptype", "Pickup")
            resp = session.get(
                cart_page,
                params={"acc": acc, "loc": loc, "shoptype": "Pickup"},
                timeout=30,
            )
        else:
            resp = session.get(cart_page, timeout=30)
        if resp.status_code != 200:
            return []
        cart_html = resp.text
    except Exception:
        return []

    cats = _categories(BeautifulSoup(cart_html, "html.parser"))

    session.headers["Referer"] = cart_page
    session.headers["X-Requested-With"] = "XMLHttpRequest"

    out = []
    for cat_id, catname in cats:
        time.sleep(REQUEST_DELAY)
        try:
            r = session.get(
                products_url,
                params={"cat": cat_id, "sel_soldout": "n", "page": "all"},
                timeout=30,
            )
            if r.status_code != 200:
                continue
            out.extend(_parse_cards(r.text, base, catname))
        except Exception:
            continue
    return out
