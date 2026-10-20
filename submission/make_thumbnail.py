"""YouTube 封面縮圖（1280x720，英文）：一眼看到賣點 = 任何故事 → 能學日文的動畫（漢字有讀音、雙字幕）。

底圖是作者自己做的學日文動畫（第三世第 1 話日英版，0:45「でも今朝、籠の中に、ジャスミンの香りがした。」），
字幕是成品原本燒的（不是後製假的）；只把字幕那一條放大，縮圖尺寸下也看得到讀音。
用法：python make_thumbnail.py   → screenshots/thumbnail_youtube.png
"""
import pathlib
import subprocess

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = pathlib.Path(__file__).resolve().parent
WS = HERE.parents[1]
SRC = WS / "ComfyUI" / "第三世動畫" / "成品" / "第01話" / "第01話_日英.mp4"
OUT = HERE / "screenshots" / "thumbnail_youtube.png"
FF = __import__("imageio_ffmpeg").get_ffmpeg_exe()
W, H = 1280, 720
BLACK = "C:/Windows/Fonts/seguibl.ttf"
BOLD = "C:/Windows/Fonts/segoeuib.ttf"
SEMI = "C:/Windows/Fonts/seguisb.ttf"
YELLOW, GREEN, WHITE = (255, 214, 64), (118, 185, 0), (255, 255, 255)

frame = HERE / "screenshots" / "_thumb_src.png"
subprocess.run([FF, "-v", "error", "-y", "-ss", "45.5", "-i", str(SRC), "-frames:v", "1", str(frame)], check=True)
src = Image.open(frame).convert("RGB")                     # 1920x1080
bg = src.resize((W, H), Image.LANCZOS)

# 上半部壓暗給標題（漸層，不蓋到人物的臉）
shade = Image.new("L", (W, H), 0)
d = ImageDraw.Draw(shade)
for y in range(H):
    a = 215 if y < 150 else max(0, int(215 * (1 - (y - 150) / 230)))
    d.line([(0, y), (W, y)], fill=a)
bg = Image.composite(Image.new("RGB", (W, H), (8, 10, 18)), bg, shade)

# 字幕條：原圖底部（讀音＋日文＋英文）放大 1.35 倍，壓在深色底上
# 取原圖中間 1/1.35 寬、整條拉滿縮圖寬 → 字放大 1.35 倍、左右沒有接縫；底色壓暗讓字跳出來
cw = int(1920 / 1.35)
sub = src.crop(((1920 - cw) // 2, 872, (1920 + cw) // 2, 1062))   # 讀音約 y=900、英文約 y=1012
sh = int(sub.height * W / cw)
sub = sub.resize((W, sh), Image.LANCZOS)
sub = Image.blend(sub, Image.new("RGB", sub.size, (6, 8, 14)), 0.45)
band_h = sh
bg.paste(sub, (0, H - band_h))
# 重新疊一次原字幕（不壓暗）：用亮度當遮罩，只把白／黃字搬回來
txt = src.crop(((1920 - cw) // 2, 872, (1920 + cw) // 2, 1062)).resize((W, sh), Image.LANCZOS)
mask = txt.convert("L").point(lambda v: 255 if v > 200 else 0).filter(ImageFilter.MaxFilter(3))
# 只留字幕所在的中間那段（窗戶反光也很亮，會被當成字搬回來）
keep = Image.new("L", mask.size, 0)
ImageDraw.Draw(keep).rectangle((120, 0, W - 120, mask.height), fill=255)
mask = Image.composite(mask, keep, keep)
bg.paste(txt, (0, H - band_h), mask)

img = bg.convert("RGBA")
d = ImageDraw.Draw(img)


def text(xy, s, font, fill, stroke=0):
    d.text(xy, s, font=font, fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0))


f1 = ImageFont.truetype(BLACK, 92)
text((48, 26), "LEARN JAPANESE", f1, WHITE, 3)
text((48, 124), "FROM ANY STORY", f1, YELLOW, 3)
f2 = ImageFont.truetype(SEMI, 34)
text((52, 236), "AI anime  ·  readings over every kanji  ·  2 subtitles", f2, WHITE, 2)
# 產品名（搜尋與評審對得上 Devpost 的專案名）
text((52, 286), "Greenlight Local", ImageFont.truetype(BOLD, 34), GREEN, 2)

# 右上角徽章：Nemotron 當導演、影片在自己的顯示卡生
# （改：舊版寫「Nemotron runs on your own GPU」不對 —— Nemotron 走 Nebius 雲端，本機跑的是影片模型）
fb = ImageFont.truetype(BOLD, 28)
label, sub2 = "NVIDIA Nemotron", "directs · your GPU renders"
bw = int(max(d.textlength(label, font=fb), d.textlength(sub2, font=ImageFont.truetype(SEMI, 22)))) + 40
d.rounded_rectangle((W - bw - 36, 34, W - 36, 120), radius=14, fill=GREEN)
d.text((W - bw - 16, 42), label, font=fb, fill=(0, 0, 0))
d.text((W - bw - 16, 82), sub2, font=ImageFont.truetype(SEMI, 22), fill=(0, 0, 0))

# 字幕條上方的小標：指出讀音
fl = ImageFont.truetype(BOLD, 24)
tag = "real output: furigana + English"
tw = d.textlength(tag, font=fl)
y0 = H - band_h - 40
d.rounded_rectangle(((W - tw) / 2 - 14, y0, (W + tw) / 2 + 14, y0 + 34), radius=8, fill=YELLOW)
d.text(((W - tw) / 2, y0 + 3), tag, font=fl, fill=(0, 0, 0))

img.convert("RGB").save(OUT, optimize=True)
frame.unlink()
print("saved", OUT, OUT.stat().st_size // 1024, "KB")
