"""Carrot CMS scraper. Adapted from the supplied CARROT scraper.

Uses the space_id / location_id / api_base already stored in the registry,
so no HTML auto-detection is needed. Each pricing option becomes its own row.
"""
import time
import uuid

from curl_cffi import requests as cffi_requests

from config import REQUEST_DELAY

DEFAULT_API = "https://api.nevada.getcarrot.io/api/v1"


def _session(site_url, space_id):
    s = cffi_requests.Session(impersonate="chrome120")
    s.headers.update({
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": site_url.rstrip("/"),
        "Referer": site_url,
        "Carrot-Space-Id": str(space_id),
        "Carrot-Anonymous-Id": str(uuid.uuid4()),
    })
    return s


def _get(session, url, params=None, retries=3):
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, params=params, timeout=30)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        time.sleep(min(2 ** attempt, 8))
    return None


def _lab_pct(lab_results):
    """Return {compound: percentage} from Carrot labResults."""
    labs = {}
    for entry in lab_results or []:
        lab_test = entry.get("labTest", {})
        compound = next(iter(lab_test), None)
        if compound == "Other":
            compound = lab_test["Other"].get("value", "Unknown")
        unit_block = entry.get("labResultUnit", {})
        unit = next(iter(unit_block), "")
        if "Percentage" in unit and compound:
            labs[compound] = entry.get("value")
    return labs


def _expand(entry):
    product = entry.get("product", {})
    options = entry.get("cashOptions") or entry.get("creditOptions") or []
    labs = _lab_pct(product.get("labResults", []))
    thc = labs.get("THC") or labs.get("THCa") or labs.get("Total THC")

    image_hashes = product.get("imageHashes", [])
    image_url = None
    for h in image_hashes:
        if isinstance(h, dict) and h.get("imageHash"):
            image_url = f"https://carrot-static.ams3.digitaloceanspaces.com/{h['imageHash']}"
            break

    base = {
        "product_id": product.get("productId"),
        "title": product.get("name"),
        "brand": product.get("brand"),
        "category": product.get("masterCategoryName") or product.get("carrotSubcategory"),
        "subcategory": product.get("subcategoryName") or product.get("carrotSubcategory"),
        "strain_type": product.get("strainType"),
        "thc_raw": str(thc) if thc is not None else None,
        "cbd_raw": str(labs.get("CBD")) if labs.get("CBD") is not None else None,
        "image_url": image_url,
        "description": (product.get("description") or "").strip() or None,
        "product_url": None,
        "raw_payload": product,
    }

    if not options:
        row = dict(base)
        row["weight_raw"] = product.get("unitWeight")
        row["price"] = product.get("posPrice")
        row["sale_price"] = None
        yield row
        return

    for o in options:
        row = dict(base)
        row["weight_raw"] = o.get("displayName") or o.get("thcWeight") or o.get("qty")
        row["price"] = o.get("price")
        row["sale_price"] = o.get("salePrice") or o.get("discountPrice")
        yield row


def scrape(dispensary: dict):
    space_id = dispensary.get("carrot_space_id")
    loc_id = dispensary.get("carrot_location_id", "1")
    api = dispensary.get("carrot_api_base") or DEFAULT_API
    site = dispensary.get("url") or "https://getcarrot.io"
    session = _session(site, space_id)

    categories = _get(session, f"{api}/store/category",
                       {"locId": loc_id, "platform": "web"})
    if not categories:
        return []
    visible = [c for c in categories if c.get("showWeb", True)]
    time.sleep(REQUEST_DELAY)

    seen, out = set(), []
    for cat in visible:
        slug = cat.get("slug")
        if not slug:
            continue
        raw = _get(session, f"{api}/store/category/slug/{slug}/product",
                   {"locId": loc_id, "platform": "web"})
        time.sleep(REQUEST_DELAY)
        for entry in raw or []:
            pid = (entry.get("product") or {}).get("productId")
            if pid and pid in seen:
                continue
            if pid:
                seen.add(pid)
            out.extend(_expand(entry))
    return out
