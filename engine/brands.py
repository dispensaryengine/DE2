"""Brand normalization. Level 1 auto-formatting + seed approved aliases.

Unresolved brand-like values stay as their Level-1 form (confidence 0.85);
exact alias hits get 0.95. A full 314-row alias table can be loaded later.
"""
import re

# Seed approved aliases drawn from the Normalization Foundation examples.
SEED_ALIASES = {
    "veteran's choice": "Veterans Choice",
    "veterans choice": "Veterans Choice",
    "vet choice": "Veterans Choice",
    "vcc": "Veterans Choice",
    "veterans choice creations": "Veterans Choice",
    "stiiizy": "Stiiizy",
    "off hours": "Off Hours",
    "electraleaf": "Electraleaf",
    "electraleaf ny": "Electraleaf",
    "pearl by gron": "Pearls by Gron",
    "pearls by gron": "Pearls by Gron",
    "6 point cannabis": "6 Points Cannabis",
    "6 points": "6 Points Cannabis",
    "house of sacci": "House of Sacci",
    "fernway": "Fernway",
    "fernway-1": "Fernway",
    "ayrloom": "Ayrloom",
    "mfny": "MFNY",
    "kings road": "Kings Road",
    "kingsroad": "Kings Road",
    "king's road": "Kings Road",
}

LOWER_WORDS = {"of", "by", "the", "and", "for"}


def _titlecase(text):
    words = text.split()
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        if i > 0 and lw in LOWER_WORDS:
            out.append(lw)
        elif w.isupper() and len(w) <= 4:   # keep short acronyms (PAX, RSO)
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:].lower())
    return " ".join(out)


def _strip_platform_suffix(text):
    return re.sub(r"-\d+$", "", text).strip()


def normalize_brand(raw_brand: str):
    """Return (canonical_name, confidence). Blank input -> (None, 0)."""
    if not raw_brand:
        return None, 0.0
    cleaned = raw_brand.replace("\u2019", "'").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = _strip_platform_suffix(cleaned)
    key = cleaned.lower()
    if key in SEED_ALIASES:
        return SEED_ALIASES[key], 0.95
    # Level 1 automatic formatting
    return _titlecase(cleaned), 0.85


def brand_key(canonical_name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (canonical_name or "").lower())
