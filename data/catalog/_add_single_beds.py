#!/usr/bin/env python3
"""往 seed_skus.json 里补单人床（香港小户型睡房净深常只有 1400-1500mm，
一张标准双人床 1490x2000 都放唔落 —— 库里的真实缺口）。"""
import json, os

P = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed_skus.json")
d = json.load(open(P, encoding="utf-8"))

NEW = [
    dict(id="ikea-hk-90300620", name_zh="SLÄKT 單人床框", name_en="SLÄKT Bed frame",
         w=990, d=2060, h=550, lightness=80, opacity=0.45, family="neutral",
         vmi=0.0902, price=1290, storage=180),
    dict(id="ikea-hk-30305708", name_zh="NEIDEN 松木單人床框", name_en="NEIDEN Bed frame",
         w=970, d=2060, h=400, lightness=72, opacity=0.4, family="warm",
         vmi=0.0818, price=890, storage=120),
    dict(id="ikea-hk-70300621", name_zh="TARVA 松木單人床框", name_en="TARVA Bed frame",
         w=990, d=2060, h=420, lightness=72, opacity=0.4, family="warm",
         vmi=0.0862, price=1190, storage=140),
    dict(id="ikea-hk-60483760", name_zh="HAUGA 單人床框", name_en="HAUGA Bed frame",
         w=990, d=2060, h=560, lightness=82, opacity=0.5, family="neutral",
         vmi=0.0826, price=1590, storage=160),
]

have = {s["id"] for s in d["skus"]}
added = 0
for n in NEW:
    if n["id"] in have:
        continue
    w, dd, h = n["w"], n["d"], n["h"]
    d["skus"].append(dict(
        id=n["id"], name_zh=n["name_zh"], name_en=n["name_en"], category="bed",
        retailer="IKEA HK",
        dims=dict(w=w, d=dd, h=h),
        footprint_L=[[0, 0], [w, 0], [w, dd], [0, dd]],
        clearance=dict(front=0, left=0, right=0, top=0),
        mount=["floor"],
        can_support=dict(max_load_kg=100, top_polygon=[[0, 0], [w, 0], [w, dd], [0, dd]]),
        visual=dict(lightness_L=n["lightness"], opacity=n["opacity"],
                    reflectivity=0.12, openness=0.6, color_family=n["family"],
                    visual_mass_index=n["vmi"]),
        price_hkd=n["price"], price_verified=False, url="", stock="in_stock",
        storage_volume_L=n["storage"], assembly="flat_pack"))
    added += 1

if isinstance(d.get("meta"), dict):
    d["meta"]["count"] = len(d["skus"])
json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("added %d, total %d skus" % (added, len(d["skus"])))
beds = sorted([(s["dims"]["w"], s["dims"]["d"], s["name_zh"]) for s in d["skus"]
               if s["category"] == "bed"])
for b in beds:
    print("   ", b)
