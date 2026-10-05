#!/usr/bin/env python3
"""端到端海报：户型图 -> 矢量化 -> 选品 -> 摆位优化 -> 3D。"""
import json
import os
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "out")
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
FONT_R = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"


def F(sz, bold=True):
    for p in ([FONT, FONT_R] if bold else [FONT_R, FONT]):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, sz)
            except Exception:
                pass
    return ImageFont.load_default()


def fit(im, w, h):
    r = min(w / im.width, h / im.height)
    im = im.resize((int(im.width * r), int(im.height * r)), Image.LANCZOS)
    c = Image.new("RGB", (w, h), "white")
    c.paste(im, ((w - im.width) // 2, (h - im.height) // 2))
    return c


def main():
    summ = json.load(open(os.path.join(OUT, "e2e_summary.json"), encoding="utf-8"))
    W, H = 1900, 1180
    sheet = Image.new("RGB", (W, H), (247, 247, 250))
    dr = ImageDraw.Draw(sheet)

    dr.text((60, 26), "從一張戶型圖到會顯大的方案", font=F(40), fill=(18, 18, 22))
    dr.text((60, 80), "戶型圖識別 → 選品 → 擺位優化 → 3D。家具一件沒少。",
            font=F(22, False), fill=(96, 96, 104))

    # 上排：原图 + 矢量化叠加
    ov = Image.open(os.path.join(OUT, "floorplan_overlay_sidebyside.png")).convert("RGB")
    dr.text((60, 132), "① 上傳戶型圖 → 矢量化", font=F(24), fill=(28, 28, 34))
    sheet.paste(fit(ov, 1780, 300), (60, 168))

    dr.text((60, 492), "② 自動選品 → ③ CP-SAT 擺位 → ④ 3D 娃娃屋", font=F(24), fill=(28, 28, 34))

    # 下排：兩種擺法的 3D 對比
    cw, ch = 870, 400
    cols = [("e2e_naive", "沒優化（只保證放得下 + 朝向）"),
            ("e2e_opt", "優化（體量往低曝光區推）")]
    for i, (tag, title) in enumerate(cols):
        x = 60 + i * (cw + 40)
        y = 528
        im = Image.open(os.path.join(OUT, "dollhouse_%s.png" % tag)).convert("RGB")
        sheet.paste(fit(im, cw, ch), (x, y))
        s = summ[tag]["score"]
        col = (46, 158, 79) if s >= 74 else (214, 69, 69)
        dr.text((x, y + ch + 10), title, font=F(21), fill=(30, 30, 34))
        dr.text((x + cw - 186, y + ch + 16), "顯大分", font=F(18, False), fill=(110, 110, 116))
        dr.text((x + cw - 122, y + ch + 2), "%d" % round(s), font=F(36), fill=col)
        dr.text((x + cw - 60, y + ch + 16), "/100", font=F(18, False), fill=(125, 125, 130))

    dr.text((60, 1000),
            "客廳 8200 × 1850 mm（戶型圖辨識置信度 0.941，比例尺 5.0 mm/px，房間 IoU 0.96）",
            font=F(16, False), fill=(90, 90, 98))
    dr.text((60, 1030),
            "求解器：OR-Tools CP-SAT（方案 §4 方法 B）。兩者都不擋門（門洞走廊侵占 0 cm²）、沙發都正對電視。",
            font=F(16, False), fill=(70, 120, 80))

    out = os.path.join(OUT, "poster_e2e.png")
    sheet.save(out)
    print("->", out, sheet.size)


if __name__ == "__main__":
    main()
