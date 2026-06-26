"""Product name extraction, PEK generation, and canonical titles.

Matching is conservative: two listings match only if their PEKs are identical.
Because the PEK encodes brand, category, form, subform, size, product name,
extract, infusion, hardware, count, profile and ratio, an exact PEK match
inherently satisfies all 10 hard gates from the PRD (no fuzzy false positives).
"""
import re

# tokens to remove from a title when extracting the product/strain name
_NOISE = [
    "live resin", "cured resin", "live rosin", "liquid diamonds",
    "distillate", "badder", "batter", "budder", "crumble",
    "honeycomb", "bubble hash", "hash hole", "hashhole", "bagel hole", "bagelhole",
    "infused", "uninfused",
    "all-in-one", "aio", "disposable", "dispo", "cartridge", "cart", "510",
    "vape pod", "pod", "vape", "starter kit",
    "pre-roll", "preroll", "pre roll", "joint", "blunt", "dogwalker",
    "flower", "smalls", "small buds", "popcorn", "shake", "ground flower",
    "moonrock", "moon rock", "gummies", "gummy", "chocolate", "beverage",
    "drink", "seltzer", "capsule", "softgel", "tablet", "lozenge", "tincture",
    "topical", "balm", "salve", "concentrate", "edible",
    "indica", "sativa", "hybrid", "dominant",
]

_SIZE_PAT = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:g|mg|gram|grams|oz|ml)\b|\.\d+\s*g\b|"
    r"\b\d+\s*(?:pk|pack|ct|count)\b|\b\d+\s*:\s*\d+\b|"
    r"\b(?:eighth|quarter|half|ounce|1/8|1/4|1/2)\b", re.I)

_THC_PAT = re.compile(r"\b\d+(?:\.\d+)?\s*%|\bthc[:%]?\s*\d*|\bcbd[:%]?\s*\d*", re.I)


def extract_product_name(title: str, brand: str | None) -> str:
    t = f" {title} "
    if brand:
        t = re.sub(re.escape(brand), " ", t, flags=re.I)
    t = _SIZE_PAT.sub(" ", t)
    t = _THC_PAT.sub(" ", t)
    for noise in sorted(_NOISE, key=len, reverse=True):
        t = re.sub(rf"\b{re.escape(noise)}\b", " ", t, flags=re.I)
    # drop separators and leftover punctuation
    t = re.sub(r"[|/\\:_*]+", " ", t)
    t = re.sub(r"[^A-Za-z0-9'&.\- ]", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -.")
    return t


def build_pek(n: dict, product_name: str) -> str:
    """n is a normalized field dict. Returns the pipe-delimited PEK."""
    brand = (n.get("normalized_brand") or "").lower().strip()
    cat = (n.get("normalized_category") or "").lower().strip()
    form = (n.get("normalized_form") or "").lower().strip()
    sub = (n.get("subform") or "").lower().strip()
    size = (n.get("normalized_size") or "").lower().strip()
    name = (product_name or "").lower().strip()
    extract = (n.get("extract_type") or "").lower().strip()
    infusion = (n.get("infusion_type") or "").lower().strip()
    hardware = (n.get("hardware_type") or "").lower().strip()
    count = str(n.get("count") or "").strip()
    profile = (n.get("cannabinoid_profile") or "").lower().strip()
    ratio = (n.get("ratio") or "").strip()
    if infusion == "none":
        infusion = ""
    parts = [brand, cat, form, sub, size, name, extract, infusion,
             hardware, count, profile, ratio]
    return "|".join(parts)


def canonical_title(n: dict, product_name: str) -> str:
    bits = []
    if n.get("normalized_brand"):
        bits.append(n["normalized_brand"])
    size = n.get("normalized_size")
    if size:
        bits.append(size)
    if n.get("count") and n.get("normalized_category") != "Edibles":
        bits.append(f"{n['count']}pk")
    if product_name:
        bits.append(product_name)
    modifier = n.get("extract_type") or (
        n.get("infusion_type") if n.get("infusion_type") not in (None, "None") else None
    ) or n.get("ratio")
    if modifier:
        bits.append(modifier)
    if n.get("hardware_type") and n.get("hardware_type") != "510 Cartridge":
        bits.append(n["hardware_type"])
    if n.get("normalized_form"):
        bits.append(n["normalized_form"])
    title = " ".join(str(b) for b in bits if b)
    return re.sub(r"\s+", " ", title).strip()
