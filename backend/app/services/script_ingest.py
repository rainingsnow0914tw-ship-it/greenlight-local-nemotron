"""劇本檔案接收：把各種格式的檔案變成純文字。

設計理由：
    入口是「丟一個檔進來」，不是看現成的資料。
    真實創作者手上的檔案格式很雜：Word 寫的、記事本存的、Markdown 筆記、
    從 Google Docs 匯出的 PDF。要求他們先轉檔就是門檻。

編碼是華語圈的真實陷阱：
    Windows 記事本存的中文 txt 很可能是 Big5 或 GBK 而不是 UTF-8。
    用 UTF-8 硬解會得到亂碼，而且不會報錯，使用者只會看到
    一堆問號然後以為系統壞了。所以要依序試多種編碼。
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path

# 依序嘗試的編碼。順序有意義：
#   utf-8-sig 先試（帶 BOM 的 UTF-8，Windows 記事本的預設之一）
#   再來是華語圈最常見的 Big5 / GBK
#   最後 latin-1 保底：它永遠不會失敗，但可能是亂碼，所以放最後
_ENCODINGS = ("utf-8-sig", "utf-8", "big5", "gb18030", "cp950", "latin-1")

SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown", ".fountain", ".docx", ".pdf"}


@dataclass
class IngestResult:
    """接收結果。"""

    text: str
    filename: str
    detected_format: str
    encoding: str | None = None
    page_count: int | None = None
    warnings: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.warnings is None:
            self.warnings = []

    @property
    def char_count(self) -> int:
        return len(self.text)

    @property
    def line_count(self) -> int:
        return len(self.text.splitlines())


class UnsupportedFormat(ValueError):
    """不支援的檔案格式。"""


def _decode_text(data: bytes) -> tuple[str, str, list[str]]:
    """把位元組解成文字，依序嘗試多種編碼。

    回傳 (文字, 用了哪個編碼, 警告)。

    亂碼偵測：latin-1 永遠解得開，所以「成功」不代表對。
    解完之後檢查有沒有大量替代字元，有的話代表編碼猜錯，
    要警告使用者而不是默默交出亂碼。
    """
    warnings: list[str] = []

    for enc in _ENCODINGS:
        try:
            text = data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue

        # 替代字元太多 = 編碼猜錯
        replacement_ratio = text.count("�") / max(len(text), 1)
        if replacement_ratio > 0.01:
            continue

        if enc == "latin-1":
            warnings.append(
                "無法確定檔案編碼，已用 latin-1 保底解讀 —— "
                "如果內容看起來像亂碼，請把檔案另存為 UTF-8 再上傳。"
            )
        elif enc not in ("utf-8", "utf-8-sig"):
            warnings.append(f"檔案編碼偵測為 {enc}（非 UTF-8），已自動轉換。")

        return text, enc, warnings

    raise UnsupportedFormat("無法解讀這個檔案的文字編碼")


def _from_docx(data: bytes) -> tuple[str, list[str]]:
    """讀 Word 檔。

    只取段落文字，不取樣式：劇本的格式資訊（粗體、置中）
    對分場判斷幫助有限，而 script_parser 本來就是靠語意在切場。

    表格內的文字也一併取出：有些人用表格排劇本的雙欄格式（左畫面右聲音）。
    """
    try:
        import docx  # python-docx
    except ImportError as exc:  # pragma: no cover
        raise UnsupportedFormat(
            "要讀 Word 檔需要安裝 python-docx：pip install python-docx"
        ) from exc

    document = docx.Document(io.BytesIO(data))
    parts: list[str] = [p.text for p in document.paragraphs]

    warnings: list[str] = []
    table_lines: list[str] = []
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                table_lines.append("\t".join(cells))

    if table_lines:
        warnings.append(
            f"文件裡有 {len(document.tables)} 個表格，內容已一併讀出 —— "
            f"如果那是雙欄劇本格式，分場結果請特別核對。"
        )
        parts.extend(table_lines)

    return "\n".join(parts), warnings


def _from_pdf(data: bytes) -> tuple[str, int, list[str]]:
    """讀 PDF。只抽文字層，不做 OCR。

    掃描版 PDF（整頁是圖片）抽不出文字，這種情況要明確告訴使用者，
    不要交出一份空白的解析結果讓他以為劇本被吃掉了。
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise UnsupportedFormat(
            "要讀 PDF 需要安裝 pypdf：pip install pypdf"
        ) from exc

    reader = PdfReader(io.BytesIO(data))
    pages = [(p.extract_text() or "") for p in reader.pages]
    text = "\n".join(pages)

    warnings: list[str] = []
    if len(text.strip()) < 50 * len(pages):
        warnings.append(
            "這份 PDF 幾乎抽不出文字，可能是掃描檔（整頁是圖片）。"
            "請改用原始的文字檔，或先做過 OCR 再上傳。"
        )

    return text, len(reader.pages), warnings


def _normalize(text: str) -> str:
    """統一換行、去掉零寬字元、壓掉過多空行。

    為什麼要壓空行：從 Word 或 PDF 轉出來的文字常常每段之間有三四個空行，
    那會讓行號膨脹、也會浪費 token。壓成最多兩個空行既保留段落感、
    又不浪費，一份劇本壓下來可能省掉 10% 的輸入成本。
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("​", "").replace("﻿", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def ingest(data: bytes, filename: str) -> IngestResult:
    """把上傳的檔案變成純文字。

    支援 .txt / .md / .fountain / .docx / .pdf。
    """
    suffix = Path(filename).suffix.lower()

    if suffix not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFormat(
            f"不支援 {suffix or '（無副檔名）'} 格式。"
            f"支援的格式：{'、'.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    if suffix == ".docx":
        raw, warnings = _from_docx(data)
        return IngestResult(
            text=_normalize(raw),
            filename=filename,
            detected_format="docx",
            warnings=warnings,
        )

    if suffix == ".pdf":
        raw, pages, warnings = _from_pdf(data)
        return IngestResult(
            text=_normalize(raw),
            filename=filename,
            detected_format="pdf",
            page_count=pages,
            warnings=warnings,
        )

    raw, encoding, warnings = _decode_text(data)
    return IngestResult(
        text=_normalize(raw),
        filename=filename,
        detected_format=suffix.lstrip("."),
        encoding=encoding,
        warnings=warnings,
    )


def estimate_parse_cost(text: str) -> dict:
    """估算解析這份劇本要花多少錢。

    上傳之後、真的解析之前，要先把這個數字給使用者看。
    解析是這條鏈路第一個花錢的動作，不能上傳完就自動扣款。

    換算率是實測校準過的：

    用「中文 1.2 字元/token」估會低估一倍多
    （實測一份 28,371 字元的劇本：估算 12,918 tokens、實際 31,817 tokens）。

    兩個原因：
      1. 行號開銷。script_parser 會給每一行加上 `1234\\t` 前綴
         （模型要靠行號回報場次範圍），兩千多行的開銷不小。
      2. 中文的 token 化沒有想像中有效率，實測約 1.12 token/字元，
         不是常見假設的 0.83。

    低估比高估糟：使用者以為只要 $0.07，看到扣了 $0.15 會覺得被騙。

    這個比率只在一份劇本上校準過。累積更多實測後要回頭修正：
    每次解析完，實際 token 數都記在 usage 裡，可以拿來比對。
    """
    from . import config_loader, rates

    # 用「非 ASCII」而不是「CJK 漢字」判斷。
    #
    # 只算 U+4E00–U+9FFF 的漢字會漏掉全形標點（，。「」！？）。
    # 實測差很多：漢字佔 64.4%，但非 ASCII 佔 77.8%。
    # 漏掉的那 13% 全是全形標點，而它們一樣要吃 token。
    non_ascii = sum(1 for ch in text if ord(ch) > 127)
    ascii_chars = len(text) - non_ascii

    # 係數由實測反推（28,371 字元 → 31,817 tokens，含行號開銷）
    input_tokens = int(non_ascii * 1.36 + ascii_chars * 0.30)
    # 輸出是場次表與角色表。實測 7,300 / 31,817 = 23%
    output_tokens = int(input_tokens * 0.23)

    pricing = config_loader.pricing().get("gemini", {})
    tier_key = pricing.get("assignments", {}).get("script_parsing", "pro")
    cfg = rates.estimate_cfg("解析劇本", pricing.get("tiers", {}).get(tier_key, {}))   # 依設定頁：Nemotron 就用 Nemotron 的價

    def _cost(inp: int, out: int) -> float:
        return (inp / 1_000_000) * float(cfg.get("input_per_1m_usd", 2.0)) + (
            out / 1_000_000
        ) * float(cfg.get("output_per_1m_usd", 12.0))

    cost = _cost(input_tokens, output_tokens)

    # 給範圍不給單一數字。
    #
    # 這個係數只在一份劇本上校準過。假裝能精確到分是不誠實的，
    # 不同題材的輸出量差很多（對白密集的劇本場次多、輸出就長）。
    #
    # UI 上顯示「約 $0.13 – $0.20」比「$0.15」誠實，
    # 而且使用者看到實際扣款落在範圍內時，對系統的信任是增加的。
    return {
        "estimated_input_tokens": input_tokens,
        "estimated_output_tokens": output_tokens,
        "estimated_cost_usd": round(cost, 3),
        "estimated_cost_low_usd": round(cost * 0.8, 3),
        "estimated_cost_high_usd": round(cost * 1.35, 3),
        "model": cfg.get("model_id"),
        "model_label": cfg.get("label"),
        "calibration_note": (
            "換算率由一份實測劇本校準（28,371 字元 → 31,817 tokens）。"
            "不同題材的輸出量差異大，實際費用可能落在範圍兩端。"
        ),
    }
