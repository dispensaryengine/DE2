"""Unit tests for the normalization + PEK engine, runnable with or without pytest.

Run:  python tests/test_engine.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.normalize import normalize, parse_grams, parse_edible_mg
from engine.brands import normalize_brand
from engine.matching import extract_product_name, build_pek


def _n(title, brand=None, category=None, weight=None):
    n = normalize({"title": title, "brand": brand, "category": category, "weight_raw": weight})
    cb, conf = normalize_brand(brand)
    n["normalized_brand"] = cb
    return n


def test_eligibility_excludes_accessories():
    n = _n("STIIIZY Battery", brand="STIIIZY", category="Accessories")
    assert n["comparison_status"] == "excluded_accessory", n["comparison_status"]


def test_eligibility_excludes_merch():
    n = _n("Dispensary Hoodie", category="Apparel")
    assert n["comparison_status"] == "excluded_merchandise"


def test_flower_category_and_size():
    n = _n("Apple Fritter Small Buds", brand="Veterans Choice",
           category="Flower", weight="7g")
    assert n["normalized_category"] == "Flower"
    assert n["subform"] == "Small Buds"
    assert n["normalized_size"] == "7g"


def test_vape_hardware_and_extract():
    n = _n("Hash Burger Live Resin 510 Cartridge 0.5g", brand="MFNY", category="Vape")
    assert n["normalized_category"] == "Vapes"
    assert n["hardware_type"] == "510 Cartridge"
    assert n["extract_type"] == "Live Resin"
    assert n["normalized_size"] == "0.5g"


def test_aio_is_disposable_vape():
    n = _n("Maui Wowie Sativa AIO", brand="Off Hours", category="Vape", weight="1g")
    assert n["hardware_type"] == "AIO"
    assert n["normalized_form"] == "Disposable Vape"


def test_prerolls_hash_hole_infusion():
    n = _n("Honey Banana Live Resin Infused Bagelhole", brand="MFNY", category="Pre-Roll")
    assert n["normalized_category"] == "Pre-Rolls"
    assert n["subform"] == "Hash Hole"
    assert n["infusion_type"] == "Live Resin Infused"


def test_edible_mg_and_count():
    n = _n("Blue Raspberry 10ct Gummies", brand="Off Hours",
           category="Edibles", weight=".1g")
    assert n["normalized_category"] == "Edibles"
    assert n["normalized_form"] == "Gummy"
    # Dutchie .1g artifact must be ignored, mg derived instead
    assert n["size_value"] is None
    n2 = _n("Blue Raspberry 100mg 10pk Gummies", category="Edibles")
    assert n2["package_thc_mg"] == 100
    assert n2["count"] == 10


def test_grams_parser():
    assert parse_grams("eighth") == 3.5
    assert parse_grams(".5g") == 0.5
    assert parse_grams("1 oz ounce") == 28.0


def test_edible_ratio():
    pkg, ratio, profile = parse_edible_mg("1:1 THC:CBD 100mg")
    assert ratio == "1:1"
    assert profile == "THC/CBD"


def test_brand_alias():
    assert normalize_brand("Veteran's Choice")[0] == "Veterans Choice"
    assert normalize_brand("STIIIZY")[0] == "Stiiizy"
    assert normalize_brand("Fernway-1")[0] == "Fernway"


def test_product_name_extraction():
    name = extract_product_name("Hash Burger Live Resin 510 Cartridge 0.5g", "MFNY")
    assert "hash burger" in name.lower()
    assert "live resin" not in name.lower()
    assert "510" not in name


def test_pek_distinguishes_size():
    a = _n("Blue Dream Live Resin Cartridge 0.5g", brand="Jaunty", category="Vape")
    b = _n("Blue Dream Live Resin Cartridge 1g", brand="Jaunty", category="Vape")
    pek_a = build_pek(a, extract_product_name(a["normalized_title"], "Jaunty"))
    pek_b = build_pek(b, extract_product_name(b["normalized_title"], "Jaunty"))
    assert pek_a != pek_b


def test_pek_distinguishes_hardware():
    a = _n("Blue Dream Cartridge 1g", brand="Jaunty", category="Vape")
    b = _n("Blue Dream Disposable 1g", brand="Jaunty", category="Vape")
    pek_a = build_pek(a, extract_product_name(a["normalized_title"], "Jaunty"))
    pek_b = build_pek(b, extract_product_name(b["normalized_title"], "Jaunty"))
    assert pek_a != pek_b


def test_pek_matches_same_product():
    a = _n("Blue Dream Live Resin Cartridge 1g", brand="Jaunty", category="Vape")
    b = _n("Blue Dream Live Resin 1g Cart", brand="Jaunty", category="Vapes")
    pek_a = build_pek(a, extract_product_name(a["normalized_title"], "Jaunty"))
    pek_b = build_pek(b, extract_product_name(b["normalized_title"], "Jaunty"))
    assert pek_a == pek_b, f"{pek_a}  !=  {pek_b}"


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {fn.__name__}: {e}")
    print(f"\n{passed}/{len(fns)} tests passed")
    return passed == len(fns)


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)
