"""使用者設定：金鑰、每個角色用哪個 AI、參數按鈕的值。存在使用者自己電腦的資料家目錄。

為什麼要有：Greenlight 原版用作者的 Google 雲端帳號付費；本機版每個人用自己的金鑰，
我們不出錢、也碰不到別人的金鑰。

金鑰怎麼存：Windows 用系統內建的 DPAPI 加密（CryptProtectData）——
只有同一個 Windows 使用者在同一台電腦上解得開，設定檔被複製走也讀不出金鑰。
其他系統（開發用）退回明文，並在設定頁標示「未加密」，不假裝安全。

角色＝各代理呼叫 genai_client.client(action=…) 帶的動作名稱，一個動作名稱就是設定頁的一列。
"""

from __future__ import annotations

import base64
import json
import sys
import threading

from .env_boot import config_home, data_home

# 動作名稱 → 角色。設定頁照這張表一列一列顯示；`needs` 說明這個角色要什麼能力（Nemotron 只吃文字）。
ROLES = {
    "解析劇本": {"role": "parse", "needs": "text"},
    "分段": {"role": "segment", "needs": "text"},
    "場面調度": {"role": "staging", "needs": "text"},
    "分鏡": {"role": "director", "needs": "text"},
    "產生提示詞": {"role": "prompts", "needs": "text"},
    "內容安全預審": {"role": "screening", "needs": "text"},
    "翻譯字幕": {"role": "subtitles", "needs": "text"},
    "提煉經驗": {"role": "learning", "needs": "text"},
    "看片檢核": {"role": "frame_review", "needs": "vision"},
    "分析音色": {"role": "voice", "needs": "audio"},
    "角色詮釋": {"role": "character", "needs": "text"},
    "生成角色候選圖": {"role": "casting", "needs": "image"},
    "台詞導演": {"role": "lines", "needs": "text"},
    "台詞審稿": {"role": "critic", "needs": "text"},   # 學語言模式：製作人（預設 Ultra）審意思有沒有走掉
    "查證診斷": {"role": "doctor", "needs": "text"},   # 生完查證沒過 → 對照症狀庫找原因、提修法（verify/doctor.py）
}

# 預設：文字工作全交給 Nemotron（Nebius Token Factory）；看圖／聽聲音／生圖 Nemotron 做不到，交給 Google。
# 模型 id 以 Token Factory 自己的 /v1/models 回報為準（問過平台）。
NEMOTRON_SUPER = "nvidia/nemotron-3-super-120b-a12b"
NEMOTRON_ULTRA = "nvidia/Nemotron-3-Ultra-550b-a55b"
NEMOTRON_NANO = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"

DEFAULTS = {
    "keys": {},                        # {"nebius": "...", "google": "..."}（存檔時加密）
    "roles": {
        "parse": {"provider": "nebius", "model": NEMOTRON_SUPER},
        "segment": {"provider": "nebius", "model": NEMOTRON_SUPER},
        "staging": {"provider": "nebius", "model": NEMOTRON_SUPER},
        "director": {"provider": "nebius", "model": NEMOTRON_SUPER},
        "prompts": {"provider": "nebius", "model": NEMOTRON_SUPER},
        "screening": {"provider": "nebius", "model": NEMOTRON_NANO},
        "subtitles": {"provider": "nebius", "model": NEMOTRON_NANO},
        "learning": {"provider": "nebius", "model": NEMOTRON_ULTRA},
        "critic": {"provider": "nebius", "model": NEMOTRON_ULTRA},
        "frame_review": {"provider": "google", "model": ""},
        "voice": {"provider": "google", "model": ""},
        # 選角拆兩格：寫外貌／音色描述（文字，Nemotron）與生角色圖（要生圖模型）。
        # 沒有生圖模型時只出文字候選，照樣能選定角色往下走（本機 H3 本來就只吃文字）。
        "character": {"provider": "nebius", "model": NEMOTRON_SUPER},
        "casting": {"provider": "local", "model": ""},
        "lines": {"provider": "nebius", "model": NEMOTRON_SUPER},   # 學語言模式的台詞導演（language/line_director.py）
        "doctor": {"provider": "nebius", "model": NEMOTRON_SUPER},  # 查證診斷（verify/doctor.py）
    },
    "engine": "local",                 # local（本機 ComfyUI）／cloud（使用者自己的 Google 金鑰）
    "comfy_url": "http://127.0.0.1:8189",
    "params": {},                      # 設定頁的按鈕值（省錢／標準／精修、4 步／8 步、解析度…）
}

_lock = threading.Lock()


def _path():
    return config_home() / "settings.json"   # 設定固定放這裡；作品位置可換，設定不跟著搬


# ── 金鑰加密（Windows DPAPI）─────────────────────────────────────────────

def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    src, dst = BLOB(len(data), buf), BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(dst)):
        raise OSError("Windows could not encrypt/decrypt the stored key")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(dst.pbData)


def encrypted() -> bool:
    """金鑰有沒有真的加密（設定頁照實顯示）。"""
    return sys.platform == "win32"


def _seal(key: str) -> str:
    if not key:
        return ""
    raw = key.encode("utf-8")
    return "dpapi:" + base64.b64encode(_dpapi(raw, True)).decode() if encrypted() else "plain:" + key


def _open(stored: str) -> str:
    if stored.startswith("dpapi:"):
        return _dpapi(base64.b64decode(stored[6:]), False).decode("utf-8")
    return stored[6:] if stored.startswith("plain:") else stored


# ── 讀寫 ──────────────────────────────────────────────────────────────

def load() -> dict:
    """設定（金鑰仍是加密的樣子）；沒有設定檔就回預設值。"""
    p = _path()
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    merged = json.loads(json.dumps(DEFAULTS))
    for k, v in data.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k].update(v)
        else:
            merged[k] = v
    return merged


def save(data: dict) -> None:
    with _lock:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)


def set_key(provider: str, key: str) -> None:
    """存一把金鑰（加密）；空字串＝刪掉。"""
    data = load()
    if key:
        data["keys"][provider] = _seal(key.strip())
    else:
        data["keys"].pop(provider, None)
    save(data)


def key(provider: str) -> str:
    """取一把金鑰的明文（只在呼叫 API 的那一刻用，不回傳給前端）。"""
    stored = load()["keys"].get(provider, "")
    return _open(stored) if stored else ""


def role_for(action: str) -> tuple[str, dict]:
    """動作名稱 → (角色, {"provider", "model"})。不在表上的動作當文字工作，走導演那一格的設定。"""
    role = ROLES.get(action, {}).get("role", "director")
    return role, load()["roles"].get(role, DEFAULTS["roles"]["director"])


def public_view() -> dict:
    """給設定頁看的版本：金鑰只顯示「有沒有、末四碼」，絕不回傳明文。"""
    data = load()
    keys = {}
    for provider, stored in data["keys"].items():
        try:
            plain = _open(stored)
            keys[provider] = {"set": True, "last4": plain[-4:]}
        except OSError:
            keys[provider] = {"set": True, "last4": "????", "error": "cannot decrypt on this machine"}
    data["keys"] = keys
    data["keys_encrypted"] = encrypted()
    data["role_table"] = [{"action": a, **r} for a, r in ROLES.items()]
    return data
