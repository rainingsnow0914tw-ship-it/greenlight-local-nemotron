"""架構圖（1920x1080，英文）：四條線 —— 導演、學語言、生成與查證、成片與學習。
顏色＝誰在做：Nemotron（綠）、程式量測／關卡（藍）、你的顯示卡（紫）、人（黃）、可編輯的知識檔（橘）。
用法：python make_architecture.py   → screenshots/architecture.png
"""
import pathlib

from PIL import Image, ImageDraw, ImageFont

OUT = pathlib.Path(__file__).resolve().parent / "screenshots" / "architecture.png"
W, H = 1920, 1080
BG, FG, DIM, LINE = (18, 20, 26), (236, 238, 242), (150, 156, 168), (120, 128, 140)
C = {"nemo": (118, 185, 0), "code": (74, 144, 226), "gpu": (156, 102, 222), "human": (240, 196, 48),
     "file": (240, 140, 60), "io": (200, 204, 212)}
B = "C:/Windows/Fonts/segoeuib.ttf"
R = "C:/Windows/Fonts/segoeui.ttf"
SB = "C:/Windows/Fonts/seguisb.ttf"
f_title, f_lane, f_box, f_sub, f_leg = (ImageFont.truetype(B, 44), ImageFont.truetype(B, 22),
                                        ImageFont.truetype(SB, 23), ImageFont.truetype(R, 18), ImageFont.truetype(R, 20))

img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)


def box(x, y, w, h, title, sub, kind):
    col = C[kind]
    d.rounded_rectangle((x, y, x + w, y + h), radius=14, fill=(28, 31, 40), outline=col, width=3)
    d.rectangle((x + 3, y + 3, x + 12, y + h - 3), fill=col)
    ty = y + 14
    for line in title.split("\n"):
        d.text((x + 24, ty), line, font=f_box, fill=FG)
        ty += 29
    for line in sub.split("\n"):
        d.text((x + 24, ty + 2), line, font=f_sub, fill=DIM)
        ty += 23
    return (x, y, w, h)


def arrow(a, b, both=False, color=LINE, label=None):
    (x1, y1), (x2, y2) = a, b
    d.line((x1, y1, x2, y2), fill=color, width=3)
    import math
    def head(px, py, qx, qy):
        ang = math.atan2(qy - py, qx - px)
        for s in (-0.45, 0.45):
            d.line((qx, qy, qx - 14 * math.cos(ang + s), qy - 14 * math.sin(ang + s)), fill=color, width=3)
    head(x1, y1, x2, y2)
    if both:
        head(x2, y2, x1, y1)
    if label:
        d.text(((x1 + x2) / 2 + 6, (y1 + y2) / 2 - 24), label, font=f_sub, fill=DIM)


def right(b):  # 右邊中點
    x, y, w, h = b
    return (x + w, y + h // 2)


def left(b):
    x, y, w, h = b
    return (x, y + h // 2)


def bottom(b, dx=0):
    x, y, w, h = b
    return (x + w // 2 + dx, y + h)


def topp(b, dx=0):
    x, y, w, h = b
    return (x + w // 2 + dx, y)


d.text((60, 30), "Greenlight Local · NVIDIA Nemotron Edition — how it works", font=f_title, fill=FG)

LX, BW, GAP = 230, 290, 50          # 五欄剛好放進 230～1880
xs = [LX + i * (BW + GAP) for i in range(5)]
RAIL = 1900                          # 右側的軌道：分鏡表／台詞 → 生成
lanes = [("DIRECT", 120), ("STUDY", 330), ("RENDER\n& CHECK", 560), ("FINISH\n& LEARN", 830)]
for name, y in lanes:
    ty = y + 40
    for ln in name.split("\n"):
        d.text((60, ty), ln, font=f_lane, fill=DIM)
        ty += 28

# 1 導演
a1 = box(xs[0], 120, BW, 150, "Your story", "novel chapter, screenplay\nor plain prose", "io")
a2 = box(xs[1], 120, BW, 150, "Nemotron 3 Super", "read · stage · direct\nshot by shot", "nemo")
a3 = box(xs[2], 120, BW, 150, "Code gates", "19 of 25 directing rules;\ndrafts loop back until\nevery gate passes", "code")
a4 = box(xs[3], 120, BW, 150, "Nemotron 3 Ultra", "producer review;\nnotes must quote the shot", "nemo")
a5 = box(xs[4], 120, BW, 150, "Shot list", "every gate passed", "io")
arrow(right(a1), left(a2))
arrow(right(a2), left(a3), both=True)
arrow(right(a3), left(a4))
arrow(right(a4), left(a5))

# 2 學語言
b1 = box(xs[0], 330, BW, 150, "Study mode", "film language +\nyour languages", "io")
b2 = box(xs[1], 330, BW, 150, "Nemotron line director", "how this character would\nsay it, to this listener", "nemo")
b3 = box(xs[2], 330, BW, 150, "Language gates", "readings checked (UniDic),\nregister, speakable; loop\nback until every gate passes", "code")
b4 = box(xs[3], 330, BW, 150, "Nemotron 3 Ultra", "meaning review\nagainst the source", "nemo")
b5 = box(xs[4], 330, BW, 150, "Lines you can study", "readings · 2 subtitles ·\nwhy it is said · notes", "io")
arrow(right(b1), left(b2))
arrow(right(b2), left(b3), both=True)
arrow(right(b3), left(b4))
arrow(right(b4), left(b5))

# 分鏡表、台詞 → 右側軌道 → 生成
d.line((right(a5)[0], right(a5)[1], RAIL, right(a5)[1]), fill=LINE, width=3)
d.line((right(b5)[0], right(b5)[1], RAIL, right(b5)[1]), fill=LINE, width=3)
d.line((RAIL, right(a5)[1], RAIL, 525), fill=LINE, width=3)
d.line((RAIL, 525, topp(c1 := (xs[0], 560, BW, 170))[0], 525), fill=LINE, width=3)
arrow((topp(c1)[0], 525), (topp(c1)[0], 560))
d.text((xs[2] + 20, 498), "shots and lines to render", font=f_sub, fill=DIM)

# 3 生成與查證
c1 = box(xs[0], 560, BW, 170, "MiniMax H3 in ComfyUI", "on your own GPU;\nQwen-Image references", "gpu")
c2 = box(xs[1], 560, BW, 170, "Local shot checks", "speech recognition (CPU),\nloudness, timing,\nface strip", "code")
c3 = box(xs[2], 560, BW, 170, "Nemotron diagnosis", "only on failed checks;\npicks from the symptom\nlibrary, must cite evidence", "nemo")
c4 = box(xs[3], 560, BW, 170, "Fixes side by side", "costs computed by the\nprogram, not the model", "code")
c5 = box(xs[4], 560, BW, 170, "You decide", "trim · re-render ·\nnew performance", "human")
arrow(right(c1), left(c2))
arrow(right(c2), left(c3))
arrow(right(c3), left(c4))
arrow(right(c4), left(c5))

# 4 成片與學習
k = box(xs[0], 830, BW, 150, "Editable knowledge", "symptoms.yaml ·\ndirecting_rules.md", "file")
l1 = box(xs[1], 830, BW, 150, "Ledger", "every check, diagnosis\nand decision", "code")
l2 = box(xs[2], 830, BW, 150, "Success-rate predictor", "learns from the ledger", "code")
f1 = box(xs[3], 830, BW, 150, "Finish", "trim → upscale → transitions\n→ join → subtitles", "code")
f2 = box(xs[4], 830, BW, 150, "A film you can study", "1080p · readings over\nkanji · 2 subtitles", "io")
arrow(right(l1), left(l2))
arrow(right(f1), left(f2))

# 人的決定：重拍（黃）、記進紀錄、通過的進成片 —— 三條各走一層，不交叉
x_re, x_log, x_ok = xs[4] + 30, xs[4] + BW // 2, xs[4] + BW - 30
d.line((x_re, 730, x_re, 752), fill=C["human"], width=3)
d.line((x_re, 752, topp(c1)[0], 752), fill=C["human"], width=3)
arrow((topp(c1)[0], 752), (topp(c1)[0], 730), color=C["human"])
d.text((xs[1] + 20, 756), "retake with the chosen fix", font=f_sub, fill=C["human"])
d.line((x_log, 730, x_log, 790), fill=LINE, width=3)
d.line((x_log, 790, topp(l1)[0], 790), fill=LINE, width=3)
arrow((topp(l1)[0], 790), (topp(l1)[0], 830))
d.text((xs[2] + 20, 794), "every decision is logged", font=f_sub, fill=DIM)
d.line((x_ok, 730, x_ok, 812), fill=LINE, width=3)
d.line((x_ok, 812, xs[3] + BW - 40, 812), fill=LINE, width=3)
arrow((xs[3] + BW - 40, 812), (xs[3] + BW - 40, 830))
d.text((x_ok - 108, 786), "approved", font=f_sub, fill=DIM)

# 知識檔 → 導演、診斷（橘色；從左側軌道進方塊上緣，不穿過別的方塊）
KR = 200
d.line((topp(k)[0] - 100, 830, topp(k)[0] - 100, 815), fill=C["file"], width=2)
d.line((topp(k)[0] - 100, 815, KR, 815), fill=C["file"], width=2)
d.line((KR, 815, KR, 104), fill=C["file"], width=2)
d.line((KR, 104, topp(a2)[0], 104), fill=C["file"], width=2)
arrow((topp(a2)[0], 104), (topp(a2)[0], 120), color=C["file"])
d.line((KR, 540, topp(c3)[0], 540), fill=C["file"], width=2)
arrow((topp(c3)[0], 540), (topp(c3)[0], 560), color=C["file"])
d.text((topp(a2)[0] + 10, 84), "rules", font=f_sub, fill=C["file"])
d.text((topp(c3)[0] + 12, 538), "symptoms", font=f_sub, fill=C["file"])

# 圖例
lx, ly = 60, 1018
for kind, name in [("nemo", "NVIDIA Nemotron (Nebius Token Factory)"), ("code", "program: gates, measurements, costs"),
                   ("gpu", "your GPU"), ("human", "you"), ("file", "plain files anyone can edit")]:
    d.rounded_rectangle((lx, ly + 4, lx + 22, ly + 26), radius=5, fill=C[kind])
    d.text((lx + 32, ly), name, font=f_leg, fill=FG)
    lx += int(d.textlength(name, font=f_leg)) + 78

img.save(OUT, optimize=True)
print("saved", OUT)
