"""把 Logo（朱印裡的草書 L，frontend/public/favicon.svg）做成 Windows 圖示 installer/greenlight.ico。

EXE、安裝檔、解除安裝清單都用這一個檔（build_tmp/GreenlightLocal.spec 的 icon=、installer/GreenlightLocal.iss 的 SetupIconFile）。
Logo 只有一個家＝favicon.svg（它又是 components/Icons.tsx <Logo/> 的固定色版本）；這裡只讀不抄。

每個尺寸都讓瀏覽器直接畫一次（不是大圖縮小：向量重畫在 16px 上比較銳利），
再把那幾張 PNG 包進 .ico（ICO 可以直接裝 PNG），不需要 Pillow。

用法（要有 playwright 的 Python，例如 nebius-director/.venv）：python scripts/make_icon.py
"""

import pathlib
import re
import struct

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
SVG = ROOT / "frontend" / "public" / "favicon.svg"
OUT = ROOT / "installer" / "greenlight.ico"
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def render(page, svg: str, size: int) -> bytes:
    page.set_viewport_size({"width": size, "height": size})
    sized = svg.replace("<svg ", f"<svg width='{size}' height='{size}' ", 1)
    page.set_content(f'<html><body style="margin:0;background:transparent">{sized}</body></html>')
    return page.screenshot(omit_background=True, clip={"x": 0, "y": 0, "width": size, "height": size})


def pack_ico(pngs: list[tuple[int, bytes]]) -> bytes:
    """ICO 檔頭＋目錄＋每張 PNG 原封不動接在後面。"""
    head = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, data = b"", b""
    for size, png in pngs:
        dim = 0 if size >= 256 else size          # 256 在 ICO 目錄裡寫 0
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    return head + entries + data


def main() -> None:
    svg = SVG.read_text(encoding="utf-8")
    # 只取印章那塊（rect 26..174）再留一點邊：整張 200×200 畫布四周有 26 的空白，縮到 16px 會小得看不見
    svg = re.sub(r'viewBox="[^"]*"', 'viewBox="20 20 160 160"', svg, count=1)
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        pngs = [(s, render(page, svg, s)) for s in SIZES]
        b.close()
    OUT.write_bytes(pack_ico(pngs))
    OUT.with_suffix(".png").write_bytes(pngs[-1][1])      # 256px 預覽
    print(f"{OUT} ({OUT.stat().st_size} bytes, sizes {SIZES})")


if __name__ == "__main__":
    main()
