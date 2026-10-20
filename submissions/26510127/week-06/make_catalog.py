"""Generate a furniture catalog of a few thousand items (deterministic, seed fixed).

    python make_catalog.py                       # -> catalog.json (all categories)

Every item: id, name (no digits, so the ask stays the last number in a message), category,
material, color, style, height_cm, width_cm, capacity (books / seats / people, when it applies),
description, reserve, list_price.
"""
import json
import random
import sys
from pathlib import Path

SEED = 6
CATEGORIES = {
    #             base reserve, height range, width range, capacity field and range
    "bookshelf": (120, (60, 210), (40, 120), ("books", (15, 160))),
    "desk":      (150, (70, 78), (80, 180), None),
    "chair":     (60, (75, 115), (40, 65), None),
    "lamp":      (25, (30, 180), (15, 50), None),
    "sofa":      (300, (70, 95), (140, 260), ("seats", (2, 5))),
    "table":     (110, (45, 78), (60, 220), ("people", (2, 10))),
    "bed":       (220, (35, 120), (90, 200), None),
    "wardrobe":  (260, (150, 220), (60, 200), None),
    "rug":       (40, (1, 2), (80, 300), None),
    "stool":     (30, (40, 80), (30, 45), None),
    "cabinet":   (140, (60, 180), (40, 120), None),
    "mirror":    (35, (40, 180), (30, 90), None),
}
MATERIALS = {"oak": 1.35, "pine": 1.0, "walnut": 1.6, "birch": 1.15, "bamboo": 1.05, "steel": 1.1,
             "aluminium": 1.05, "glass": 1.2, "rattan": 0.95, "plastic": 0.7, "marble": 1.8, "velvet": 1.3,
             "leather": 1.7, "linen": 1.1, "wool": 1.25}
WOOD = {"oak", "pine", "walnut", "birch", "bamboo"}
COLORS = ["natural", "white", "black", "grey", "navy", "olive", "terracotta", "cream", "charcoal", "honey"]
STYLES = ["Nordic", "Industrial", "Classic", "Minimal", "Rustic", "Mid-century", "Japandi", "Coastal",
          "Loft", "Studio", "Heritage", "Urban"]
SIZES = [("Compact", 0.75), ("Standard", 1.0), ("Wide", 1.25), ("Tall", 1.2), ("Low", 0.85), ("Grand", 1.5)]
FITS = {"bookshelf": ["pine", "oak", "walnut", "birch", "bamboo", "steel", "glass"],
        "desk": ["oak", "pine", "walnut", "birch", "steel", "glass", "bamboo"],
        "chair": ["oak", "pine", "steel", "plastic", "rattan", "leather", "velvet", "bamboo"],
        "lamp": ["steel", "aluminium", "glass", "rattan", "bamboo", "linen", "marble"],
        "sofa": ["velvet", "leather", "linen", "wool"],
        "table": ["oak", "pine", "walnut", "marble", "glass", "steel", "birch"],
        "bed": ["oak", "pine", "walnut", "steel", "velvet", "linen"],
        "wardrobe": ["oak", "pine", "walnut", "birch", "steel"],
        "rug": ["wool", "linen", "rattan", "bamboo"],
        "stool": ["oak", "pine", "steel", "plastic", "rattan", "bamboo"],
        "cabinet": ["oak", "pine", "walnut", "birch", "steel", "glass"],
        "mirror": ["oak", "steel", "aluminium", "rattan", "glass"]}


def make(n_per_category=260, seed=SEED):
    rnd = random.Random(seed)
    items, seen = [], set()
    for cat, (base, hr, wr, cap) in CATEGORIES.items():
        made = 0
        while made < n_per_category:
            mat, color, style = rnd.choice(FITS[cat]), rnd.choice(COLORS), rnd.choice(STYLES)
            size, sf = rnd.choice(SIZES)
            name = f"{style} {size} {mat.title()} {cat.title()} in {color.title()}"
            if name in seen:
                continue
            seen.add(name)
            h = rnd.randint(*hr)
            w = rnd.randint(*wr)
            if size == "Tall":
                h = min(hr[1], int(h * 1.2) + 5)
            if size == "Low":
                h = max(hr[0], int(h * 0.8))
            item = {"id": f"{cat[:3]}-{len(items):05d}", "name": name, "category": cat, "material": mat,
                    "color": color, "style": style, "height_cm": h, "width_cm": w}
            desc = [f"{style.lower()} {cat} made of {mat}" + (" (solid wood)" if mat in WOOD else ""),
                    f"{color} finish", f"{h} cm high, {w} cm wide"]
            if cap:
                field, (lo, hi) = cap
                if field == "books":                      # capacity follows the size of the shelf
                    v = max(lo, min(hi, int(h * w / 75 + rnd.randint(-8, 8))))
                else:
                    v = rnd.randint(lo, hi)
                item[field] = v
                desc.append(f"holds about {v} books" if field == "books" else f"{v} {field}")
            item["description"] = ", ".join(desc)
            reserve = base * MATERIALS[mat] * sf * rnd.uniform(0.85, 1.15)
            item["reserve"] = int(round(reserve / 5) * 5)
            item["list_price"] = int(round(item["reserve"] * rnd.uniform(1.3, 1.6) / 5) * 5)
            items.append(item)
            made += 1
    return items


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("catalog.json")
    items = make()
    out.write_text(json.dumps(items, indent=0, ensure_ascii=False), encoding="utf-8")
    print(f"{len(items)} items -> {out}")
