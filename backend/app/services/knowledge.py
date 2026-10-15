"""知識庫：症狀庫、導演規則 —— 放在安裝檔外面、使用者隨時可以改的地方。

安裝檔只是方便使用的外殼；知識要能隨時修改，所以放在安裝檔外面。
→ 程式（EXE）只是外殼；系統越用越強靠的是這些知識，不能封死在安裝檔裡（_MEIPASS 每次啟動都是唯讀的新解壓）。

做法（一件事一個家）：
  - 知識的家＝ config_home()/knowledge/（Windows：%LOCALAPPDATA%\\GreenlightLocal\\knowledge）
  - 第一次啟動把安裝檔裡的預設版複製過去；之後一律讀那裡的（改完下一次用到就生效，不用重開、不用重打包）
  - 使用者改壞了（YAML 格式錯）→ 退回預設版、設定頁照實講哪一個檔哪裡壞（不默默吞掉）
  - 設定頁可以「打開資料夾」「還原預設」
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from .env_boot import config_home, project_root

logger = logging.getLogger(__name__)

# 名稱 → 安裝檔裡的預設版。要讓使用者能改的知識才放這裡（門檻、程式邏輯不放）
FILES = {
    "symptoms.yaml": lambda: project_root() / "config" / "symptoms.yaml",
    "directing_rules.md": lambda: Path(__file__).resolve().parent.parent / "director" / "rules" / "directing_rules.md",
}
_last_error: dict[str, str] = {}


def home() -> Path:
    d = config_home() / "knowledge"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_path(name: str) -> Path:
    return FILES[name]()


def _pristine(name: str) -> Path:
    """複製給使用者時的預設版原樣（藏在 .defaults/）：用來分辨「使用者改過」還是「只是舊版預設」。"""
    d = home() / ".defaults"
    d.mkdir(exist_ok=True)
    return d / name


def path(name: str) -> Path:
    """使用者那一份（沒有就先從預設版複製一份過去）。

    新版安裝檔的預設改了（例：症狀庫補英文），而使用者那份跟當初複製時一字不差 = 沒改過
    → 自動換成新預設；改過的一律不動。不這樣做的話，裝過舊版的人永遠停在第一次啟動時的知識。"""
    p, src, keep = home() / name, default_path(name), _pristine(name)
    if not src.exists():
        return p if p.exists() else src
    if not p.exists():
        shutil.copyfile(src, p)
        shutil.copyfile(src, keep)
    elif keep.exists() and p.read_bytes() == keep.read_bytes() and p.read_bytes() != src.read_bytes():
        shutil.copyfile(src, p)
        shutil.copyfile(src, keep)
        logger.info("knowledge %s was unedited — updated to the new bundled default", name)
    return p


def read_text(name: str) -> str:
    return path(name).read_text(encoding="utf-8")


def load_yaml(name: str) -> dict:
    """讀使用者那一份；格式壞了就退回預設版，並記下錯誤給設定頁看。"""
    import yaml

    p = path(name)
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("top level must be a mapping")
        _last_error.pop(name, None)
        return data
    except Exception as exc:  # noqa: BLE001 - 使用者改壞了：退回預設，但要講出來
        _last_error[name] = f"{type(exc).__name__}: {str(exc)[:300]}"
        logger.warning("knowledge %s is broken (%s) — using the bundled default", p, exc)
        return yaml.safe_load(default_path(name).read_text(encoding="utf-8"))


def status() -> dict:
    """設定頁看的：資料夾在哪、每個檔有沒有被改過、能不能讀。"""
    out = []
    for name in FILES:
        p = path(name)
        if name.endswith(".yaml"):
            load_yaml(name)
        base = _pristine(name) if _pristine(name).exists() else default_path(name)
        edited = p != default_path(name) and base.exists() and p.read_bytes() != base.read_bytes()
        out.append({"name": name, "path": str(p), "edited": edited, "error": _last_error.get(name, "")})
    return {"dir": str(home()), "files": out}


def reset(name: str) -> None:
    """還原成安裝檔裡的預設版（使用者改的那份先改名留著，不直接覆蓋掉）。"""
    p = home() / name
    if p.exists():
        p.replace(p.with_name(p.name + ".bak"))
    shutil.copyfile(default_path(name), p)
    shutil.copyfile(default_path(name), _pristine(name))
    _last_error.pop(name, None)
