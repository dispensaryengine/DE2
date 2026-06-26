"""Normalization engine: raw DPL -> normalized fields + comparison_status.

Implements the rules from the Normalization Foundation: text cleaning,
cannabis eligibility gate, 7-category minimization, form/subform, size in
grams, edible mg/count/ratio, and extract/infusion/hardware classification.
"""
import re
import unicodedata

# ---- exclusion keywords (accessories / merch / non-cannabis) ----
EXCLUDE_KEYWORDS = [
    "battery", "charger", "dab tool", "carb cap", "terp pearl", "rolling paper",
    "papers", "cone", "filter tip", "lighter", "torch", "grinder", "tray",
    "scale", "container", "stash jar", "smell-proof", "pipe", "bong", "rig",
    "bubbler", "glassware", "t-shirt", "shirt", "hoodie", "crewneck", "beanie",
    "snapback", "hat", "sticker", "pin ", "apparel", "merch", "wrap (empty)",
]

DECORATIONS = [
    "sale", "new", "staff pick", "best seller", "limited", "bogo",
    "online only", "vendor day", "fresh drop",
]

# ---- category keyword -> canonical category ----
CATEGORY_RULES = [
    ("Pre-Rolls", ["pre-roll", "preroll", "pre roll", "joint", "blunt", "hash hole",
                   "hash-hole", "hashhole", "bagel hole", "bagelhole", "dogwalker"]),
    ("Vapes", ["vape", "vaporizer", "cartridge", "cart ", "510", "disposable",
               "dispo", "all-in-one", "aio", "pod"]),
    ("Concentrates", ["concentrate", "extract", "dab", "live resin", "cured resin",
                      "live rosin", "rosin", "badder", "batter", "budder", "sauce",
                      "diamond", "crumble", "honeycomb", "bubble hash", "hash",
                      "kief", "keef", "rso", "rick simpson"]),
    ("Edibles", ["edible", "gummy", "gummies", "chocolate", "beverage", "drink",
                 "seltzer", "tea", "lemonade", "capsule", "softgel", "pill",
                 "tablet", "lozenge", "mints", "hard candy", "baked good",
                 "cookie", "brownie", "bar ", "oral", "syrup"]),
    ("Topicals", ["topical", "balm", "salve", "lotion", "cream", "transdermal"]),
    ("Tinctures", ["tincture", "drops", "sublingual"]),
    ("Flower", ["flower", "bud", "nug", "smalls", "small buds", "popcorn", "shake",
                "trim", "ground flower", "moonrock", "moon rock", "eighth"]),
]

EXTRACT_RULES = [
    ("Liquid Diamonds", ["liquid diamond"]),
    ("Live Resin", ["live resin"]),
    ("Cured Resin", ["cured resin"]),
    ("Live Rosin", ["live rosin"]),
    ("Rosin", ["rosin"]),
    ("Resin", ["resin"]),
    ("Diamonds", ["diamond"]),
    ("Badder", ["badder", "batter"]),
    ("Budder", ["budder"]),
    ("Sauce", ["sauce"]),
    ("Crumble", ["crumble", "honeycomb"]),
    ("Bubble Hash", ["bubble hash"]),
    ("Hash", ["hash"]),
    ("Kief", ["kief", "keef"]),
    ("RSO", ["rso", "rick simpson"]),
    ("Distillate", ["distillate"]),
]

INFUSION_RULES = [
    ("Live Resin Infused", ["live resin infused", "infused live resin"]),
    ("Live Rosin Infused", ["live rosin infused", "infused live rosin"]),
    ("Rosin Infused", ["rosin infused"]),
    ("Hash Infused", ["hash infused", "hash hole", "hash-hole", "bagel hole", "bagelhole"]),
    ("Kief Infused", ["kief infused"]),
    ("Diamond Infused", ["diamond infused"]),
    ("Infused", ["infused"]),
]

HARDWARE_RULES = [
    ("AIO", ["all-in-one", "aio"]),
    ("Disposable", ["disposable", "dispo"]),
    ("Pod", [" pod", "vape pod"]),
    ("510 Cartridge", ["510", "cartridge", "cart"]),
    ("Vape Kit", ["starter kit", "vape kit"]),
]

DOMINANCE_RULES = [
    ("Indica Hybrid", ["indica hybrid", "indica-hybrid", "indica dominant"]),
    ("Sativa Hybrid", ["sativa hybrid", "sativa-hybrid", "sativa dominant"]),
    ("Indica", ["indica"]),
    ("Sativa", ["sativa"]),
    ("Hybrid", ["hybrid"]),
    ("CBD", ["cbd"]),
]

FLOWER_SUBFORM = [
    ("Small Buds", ["small buds", "smalls", "popcorn", "minis"]),
    ("Shake", ["shake", "trim", "sugar leaf"]),
    ("Ground Flower", ["ground flower", "ready-to-roll", "milled"]),
    ("Moonrocks", ["moonrock", "moon rock"]),
]


def _strip_accents(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(c))


def clean_text(text):
    if not text:
        return ""
    text = _strip_accents(str(text))
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def strip_decorations(text):
    out = text
    for d in DECORATIONS:
        out = re.sub(rf"\b{re.escape(d)}\b", "", out, flags=re.I)
    return re.sub(r"\s+", " ", out).strip(" -|:")


def search_form(text):
    return re.sub(r"[^a-z0-9 ]", " ", clean_text(text).lower())


def _first_match(haystack, rules):
    for value, keywords in rules:
        for kw in keywords:
            if kw in haystack:
                return value
    return None


def eligibility(title, category):
    blob = f"{category or ''} {title or ''}".lower()
    for kw in EXCLUDE_KEYWORDS:
        if kw in blob:
            # apparel/merch vs accessory split
            if any(m in kw for m in ("shirt", "hoodie", "hat", "beanie",
                                     "sticker", "apparel", "merch", "crewneck",
                                     "snapback", "pin ")):
                return "excluded_merchandise"
            return "excluded_accessory"
    return "eligible_cannabis"


def map_category(title, raw_category):
    blob = f"{raw_category or ''} {title or ''}".lower()
    cat = _first_match(blob, CATEGORY_RULES)
    return cat


# ---- size parsing ----
GRAM_WORDS = {
    "eighth": 3.5, "1/8": 3.5, "quarter": 7.0, "1/4": 7.0,
    "half oz": 14.0, "half ounce": 14.0, "1/2 oz": 14.0,
    "ounce": 28.0, "1 oz": 28.0, "half gram": 0.5,
}


def parse_grams(text):
    t = text.lower()
    m = re.search(r"(\d+\.\d+|\.\d+|\d+)\s*g\b", t)
    if m:
        val = m.group(1)
        return float("0" + val) if val.startswith(".") else float(val)
    for word, grams in GRAM_WORDS.items():
        if word in t:
            return grams
    return None


def parse_count(text):
    m = re.search(r"(\d+)\s*(?:pk|pack|ct|count|x\b)", text.lower())
    if m:
        return int(m.group(1))
    return None


def parse_edible_mg(text):
    t = text.lower()
    ratio = None
    rm = re.search(r"(\d+)\s*:\s*(\d+)", t)
    if rm:
        ratio = f"{rm.group(1)}:{rm.group(2)}"
    profile = "THC"
    if "cbd" in t:
        profile = "THC/CBD"
    elif "cbn" in t:
        profile = "THC/CBN"
    pkg = None
    m = re.search(r"(\d+(?:\.\d+)?)\s*mg", t)
    if m:
        pkg = float(m.group(1))
    return pkg, ratio, profile


def normalize(raw: dict) -> dict:
    """raw is a scraper row. Returns normalized field dict."""
    title = clean_text(raw.get("title"))
    norm_title = strip_decorations(title)
    raw_cat = raw.get("category")
    weight_raw = raw.get("weight_raw")
    blob = search_form(f"{title} {raw_cat or ''} {weight_raw or ''}")

    status = eligibility(title, raw_cat)
    category = map_category(title, raw_cat)
    if status == "eligible_cannabis" and not category:
        status = "needs_review"

    result = {
        "normalized_title": norm_title,
        "normalized_category": category,
        "comparison_status": status,
        "normalized_form": None,
        "subform": None,
        "extract_type": None,
        "infusion_type": None,
        "hardware_type": None,
        "dominance_or_type": _first_match(blob, DOMINANCE_RULES),
        "size_value": None,
        "size_unit": None,
        "normalized_size": None,
        "count": None,
        "package_thc_mg": None,
        "serving_thc_mg": None,
        "cannabinoid_profile": None,
        "ratio": None,
    }

    if status != "eligible_cannabis" or not category:
        return result

    # form / subform / hardware / extract / infusion
    if category == "Flower":
        result["normalized_form"] = "Flower"
        result["subform"] = _first_match(blob, FLOWER_SUBFORM)
    elif category == "Pre-Rolls":
        result["normalized_form"] = "Pre-Roll"
        if any(k in blob for k in ("hash hole", "hashhole", "bagel hole", "bagelhole")):
            result["subform"] = "Hash Hole"
        elif "blunt" in blob:
            result["subform"] = "Blunt"
        result["infusion_type"] = _first_match(blob, INFUSION_RULES) or "None"
        result["extract_type"] = _first_match(blob, EXTRACT_RULES)
    elif category == "Vapes":
        hw = _first_match(blob, HARDWARE_RULES) or "510 Cartridge"
        result["hardware_type"] = hw
        result["normalized_form"] = {
            "AIO": "Disposable Vape", "Disposable": "Disposable Vape",
            "Pod": "Vape Pod", "Vape Kit": "Vape Kit",
        }.get(hw, "Vape Cartridge")
        result["extract_type"] = _first_match(blob, EXTRACT_RULES)
    elif category == "Concentrates":
        result["normalized_form"] = "Concentrate"
        result["extract_type"] = _first_match(blob, EXTRACT_RULES)
    elif category == "Edibles":
        form = _first_match(blob, [
            ("Gummy", ["gummy", "gummies"]),
            ("Chocolate", ["chocolate"]),
            ("Beverage", ["beverage", "drink", "seltzer", "tea", "lemonade"]),
            ("Capsule", ["capsule", "softgel", "pill"]),
            ("Tablet", ["tablet"]),
            ("Lozenge", ["lozenge", "mints", "hard candy"]),
            ("Baked Good", ["cookie", "brownie", "baked"]),
        ]) or "Edible"
        result["normalized_form"] = form
    elif category == "Topicals":
        result["normalized_form"] = "Topical"
        if "transdermal" in blob:
            result["subform"] = "Transdermal"
    elif category == "Tinctures":
        result["normalized_form"] = "Tincture"

    # size
    if category == "Edibles":
        pkg, ratio, profile = parse_edible_mg(f"{title} {weight_raw or ''}")
        cnt = parse_count(f"{title} {weight_raw or ''}")
        result["package_thc_mg"] = pkg
        result["ratio"] = ratio
        result["cannabinoid_profile"] = profile
        result["count"] = cnt
        if pkg:
            result["normalized_size"] = f"{int(pkg) if pkg == int(pkg) else pkg}mg"
    else:
        grams = parse_grams(f"{weight_raw or ''} {title}")
        # ignore Dutchie edible-style artifacts on non-edibles
        if grams is not None and grams >= 0.05:
            result["size_value"] = grams
            result["size_unit"] = "g"
            gs = int(grams) if grams == int(grams) else grams
            result["normalized_size"] = f"{gs}g"
        result["count"] = parse_count(f"{title} {weight_raw or ''}")

    return result
