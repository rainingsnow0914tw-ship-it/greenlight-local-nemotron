"""名字表一行的拆法（唯一的家）：「名字=原聲唸法/英文寫法/個性」，後兩段可省。

例：深息=シェンシー/Shenxi/溫柔的男生，自稱僕。
台詞導演（jobs）和介面翻譯（ui_translate）都從這裡拆 —— 以前翻譯那邊自己拆、只切第一個「/」，
加了第四段個性之後英文名變成「Shenxi/溫柔的男生，自稱僕」，被貼進英文介面的每一句說明（範例 2 抓到）。
"""
from __future__ import annotations


def rows(text: str | None, need_eq: bool = False):
    """逐行產生 (名字, 原聲唸法, 英文寫法, 個性)。名字空白的行跳過；need_eq＝沒有「=」的行也跳過。"""
    for row in (text or "").splitlines():
        if need_eq and "=" not in row:
            continue
        name, _, rest = row.partition("=")
        parts = [p.strip() for p in rest.split("/", 2)] + ["", ""]
        if name.strip():
            yield name.strip(), parts[0], parts[1], parts[2]
