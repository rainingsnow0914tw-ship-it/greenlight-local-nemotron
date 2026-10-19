"""設定頁與機器檢查的 API。

- GET  /api/settings              目前設定（金鑰只給「有沒有、末四碼」，絕不回傳明文）
- PUT  /api/settings/key          存／刪一把金鑰（加密存在使用者電腦）
- PUT  /api/settings/roles        每個步驟用哪個 AI
- PUT  /api/settings/params       參數按鈕的值、引擎（本機／雲端）、ComfyUI 位址
- POST /api/settings/test/{prov}  驗證金鑰：只呼叫平台的「列出模型」—— 不生成任何東西、不花錢
- GET  /api/machine/check         機器檢查（顯示卡、顯存、驅動、硬碟、ComfyUI、模型）
"""

from __future__ import annotations

import pathlib

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import demo_mode, settings_store

router = APIRouter()

PROVIDERS = {"nebius", "google"}


class KeyIn(BaseModel):
    provider: str
    key: str = ""


class RoleIn(BaseModel):
    roles: dict[str, dict]


class ParamsIn(BaseModel):
    engine: str | None = None
    comfy_url: str | None = None
    params: dict | None = None


@router.get("")
def get_settings():
    return settings_store.public_view()


@router.put("/key")
def put_key(body: KeyIn):
    if body.provider not in PROVIDERS:
        raise HTTPException(400, f"unknown provider {body.provider!r}")
    settings_store.set_key(body.provider, body.key)
    return settings_store.public_view()


@router.put("/roles")
def put_roles(body: RoleIn):
    data = settings_store.load()
    for role, cfg in body.roles.items():
        if role not in data["roles"]:
            raise HTTPException(400, f"unknown role {role!r}")
        if cfg.get("provider") not in PROVIDERS | {"local"}:
            raise HTTPException(400, f"unknown provider for {role}: {cfg.get('provider')!r}")
        if cfg.get("provider") == "google":
            # 雲端入口不開放（Coming soon）—— 少一個要維護的東西；畫面上也是灰的
            raise HTTPException(400, "cloud providers are coming soon — this edition runs Nemotron and your local GPU")
        data["roles"][role] = {"provider": cfg["provider"], "model": cfg.get("model", "")}
    settings_store.save(data)
    return settings_store.public_view()


@router.put("/params")
def put_params(body: ParamsIn):
    data = settings_store.load()
    if body.engine is not None:
        if body.engine not in ("local", "cloud"):
            raise HTTPException(400, "engine must be local or cloud")
        if body.engine == "cloud":
            raise HTTPException(400, "the cloud engine is coming soon — this edition renders on your local GPU")
        data["engine"] = body.engine
    if body.comfy_url is not None:
        data["comfy_url"] = body.comfy_url.rstrip("/")
    if body.params is not None:
        data["params"].update(body.params)
    settings_store.save(data)
    return settings_store.public_view()


# ── 存放位置：作品放哪、成品輸出到哪（使用者可以自己選資料夾）──────────

_DATA_PARTS = ["projects", "media", "learning.db", "director_learning", "craft_notes"]


class StorageIn(BaseModel):
    data_dir: str | None = None
    move: bool = True
    export_dir: str | None = None


def _storage_view() -> dict:
    import shutil

    from ..services.env_boot import config_home, data_home

    d = data_home()
    d.mkdir(parents=True, exist_ok=True)
    export = settings_store.load().get("export_dir") or str(d / "exports")
    return {"data_dir": str(d), "default_dir": str(config_home()), "export_dir": export,
            "free_gb": round(shutil.disk_usage(d).free / 2**30, 1)}


@router.get("/storage")
def get_storage():
    return _storage_view()


@router.put("/storage")
def put_storage(body: StorageIn):
    """換作品資料夾（move=True 會把現有作品一起搬過去，搬完才切換；失敗就不切換）＋設定成品輸出資料夾。"""
    import json
    import shutil
    from pathlib import Path

    from ..services import ledger, project_store
    from ..services.env_boot import config_home, data_home

    if body.data_dir:
        new, old = Path(body.data_dir), data_home()
        try:
            new.mkdir(parents=True, exist_ok=True)
            (new / ".write_test").write_text("ok", encoding="utf-8")
            (new / ".write_test").unlink()
        except OSError as exc:
            raise HTTPException(400, f"cannot write to {new}: {exc}") from exc
        if new.resolve() != old.resolve():
            if body.move:
                for part in _DATA_PARTS:
                    src = old / part
                    if src.is_dir():
                        shutil.copytree(src, new / part, dirs_exist_ok=True)
                    elif src.exists():
                        shutil.copy2(src, new / part)
            (config_home() / "location.json").write_text(json.dumps({"data_dir": str(new)}, ensure_ascii=False),
                                                         encoding="utf-8")
            # 已經開著的「專案儲存」和「學習紀錄」還指著舊位置 —— 重設，下一次用到就開新位置
            project_store._store = None  # noqa: SLF001
            ledger.reset_connection()
    if body.export_dir is not None:
        data = settings_store.load()
        data["export_dir"] = body.export_dir
        settings_store.save(data)
    return _storage_view()


@router.post("/test/{provider}")
def test_key(provider: str):
    """金鑰能不能用、能用哪些模型。只問「列出模型」，不生成、不花錢。"""
    key = settings_store.key(provider)
    if not key:
        return {"ok": False, "reason": "no key saved"}
    try:
        if provider == "nebius":
            r = httpx.get("https://api.tokenfactory.nebius.com/v1/models",
                          headers={"Authorization": f"Bearer {key}"}, timeout=20)
            ids = [m["id"] for m in r.json().get("data", [])] if r.status_code == 200 else []
            nemotron = [i for i in ids if "nemotron" in i.lower()]
        elif provider == "google":
            r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models",
                          params={"pageSize": 200}, headers={"x-goog-api-key": key}, timeout=20)
            ids = [m["name"].split("/")[-1] for m in r.json().get("models", [])] if r.status_code == 200 else []
            nemotron = []
        else:
            raise HTTPException(400, f"unknown provider {provider!r}")
    except httpx.HTTPError as exc:
        return {"ok": False, "reason": f"could not reach the platform: {type(exc).__name__}"}
    if r.status_code != 200:
        return {"ok": False, "reason": f"the platform refused the key (HTTP {r.status_code})"}
    video = [i for i in ids if "veo" in i or "omni" in i]
    return {"ok": True, "models": len(ids), "nemotron": nemotron, "video_models": video}


machine_router = APIRouter()


# ── 選資料夾、打開資料夾（Windows 自己的對話框；伺服器只聽本機 127.0.0.1，外面的人叫不到）──────

class FolderIn(BaseModel):
    initial: str | None = None
    path: str | None = None


@machine_router.post("/pick-folder")
def pick_folder(body: FolderIn):
    """跳出 Windows 的「選擇資料夾」視窗，回傳選的路徑（按取消回 null）。

    用 tkinter 在自己的執行緒開：網頁畫面不能直接開系統對話框，後端跟使用者在同一台電腦上就可以。
    """
    import threading

    result: dict = {"path": None}

    def ask():
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)       # 不要被 app 視窗蓋住
        result["path"] = filedialog.askdirectory(parent=root, initialdir=body.initial or None, mustexist=False) or None
        root.destroy()

    t = threading.Thread(target=ask, name="pick-folder")
    t.start()
    t.join()
    return result


@router.get("/knowledge")
def knowledge_status():
    """知識庫（症狀庫、導演規則）在哪、有沒有被改過、能不能讀 —— 安裝檔外面、隨時可改（services/knowledge.py）。"""
    from ..services import knowledge

    return knowledge.status()


@router.post("/knowledge/reset")
def knowledge_reset(body: dict):
    """還原一個知識檔成預設版（改過的那份留成 .bak）。"""
    from ..services import knowledge

    name = body.get("name")
    if name not in knowledge.FILES:
        raise HTTPException(400, f"name must be one of {list(knowledge.FILES)}")
    knowledge.reset(name)
    return knowledge.status()


@machine_router.post("/open-folder")
def open_folder(body: FolderIn):
    """用檔案總管打開資料夾（不存在就先建）。"""
    import os
    from pathlib import Path

    p = Path(body.path or "")
    if not body.path:
        raise HTTPException(400, "no folder given")
    p.mkdir(parents=True, exist_ok=True)
    if hasattr(os, "startfile"):
        os.startfile(str(p))  # noqa: S606 - 本機 app 打開使用者自己的資料夾
    return {"opened": str(p)}


@machine_router.get("/check")
def machine_check(drive: str = "C:\\"):
    from ..machine import doctor

    return doctor.run(settings_store.comfy_url(), drive)


# ── 一鍵安裝（背景跑，畫面輪詢進度）──────────────────────────────────────
#
# 為什麼要背景：下載 ComfyUI 2GB＋模型 42GB，可能要一兩個小時；請求卡著等會逾時、畫面也不知道在幹嘛。
# 進度只放記憶體（_install）：安裝本身可續傳（已下載、已驗證的檔會跳過），程式重開再按一次就接著裝。

_install: dict = {"status": None, "log": [], "target": None}


class InstallIn(BaseModel):
    target: str
    accept_license: bool = False
    reuse_models: str | None = None
    start: bool = True


@machine_router.get("/license")
def machine_license():
    from ..machine import stack

    return {"url": stack.LICENSE_URL, "excluded_regions": stack.LICENSE_EXCLUDED,
            "models": [{"name": n, "gb": round(m["size"] / 2**30, 1), "folder": m["folder"]}
                       for n, m in stack.MODELS.items() if not m.get("unverified")]}


@machine_router.post("/install/cancel")
def machine_install_cancel():
    """取消一鍵安裝：下載中的檔案停在這裡（.part 留著，下次按安裝接著下）。"""
    from ..machine import installer

    installer.CANCEL.set()
    return {"cancelling": _install["status"] == "running"}


@machine_router.post("/install")
def machine_install(body: InstallIn):
    import threading

    from ..machine import doctor, installer

    if _install["status"] == "running":
        return _install
    if not body.accept_license:
        raise HTTPException(400, "accept the MiniMax H3 license first")
    g = doctor.gpu()
    cap = g["compute_cap"] if g else 12.0
    _install.update(status="running", log=[], target=body.target)
    installer.CANCEL.clear()

    def run():
        try:
            for ev in installer.install(body.target, True, compute_cap=cap, reuse_models=body.reuse_models,
                                        start=body.start):
                ev = {k: v for k, v in ev.items() if k != "report"} | ({"ready": ev["report"]["ready"]} if ev.get("report") else {})
                # 同一步的「進行中」只留最新一筆，畫面才不會被下載進度洗版
                if _install["log"] and _install["log"][-1]["step"] == ev["step"] and _install["log"][-1]["status"] == "running":
                    _install["log"][-1] = ev
                else:
                    _install["log"].append(ev)
            _install["status"] = "done"
            # 裝在哪裡、位址是什麼都記下來：之後每次開 app，沒在跑就自己啟動（installer.ensure_running）
            data = settings_store.load()
            data["comfy_home"] = body.target
            data["comfy_url"] = f"http://127.0.0.1:{installer.DEFAULT_PORT}"
            settings_store.save(data)
        except installer.InstallCancelled as exc:
            _install["log"].append({"step": "cancelled", "status": "cancelled", "detail": f"cancelled during {exc}"})
            _install["status"] = "cancelled"
        except demo_mode.DemoBlocked:
            # 專案規矩（接線檢查會查）：籠統 except 之前一律先放行 DemoBlocked
            _install["status"] = "failed"
            raise
        except Exception as exc:  # noqa: BLE001 - 錯誤留在畫面上，不默默消失
            _install["log"].append({"step": "error", "status": "failed", "detail": f"{type(exc).__name__}: {str(exc)[:300]}"})
            _install["status"] = "failed"

    threading.Thread(target=run, daemon=True, name="greenlight-install").start()
    return _install


@machine_router.get("/install")
def machine_install_state():
    return _install


# ── 生圖模型包（Qwen-Image 2512）下載進使用者的 ComfyUI ───────────────────────
#
# 使用者未必懂 ComfyUI 怎麼操作 → app 自己問 ComfyUI 模型放哪、自己下載、畫面看進度。
# 跟一鍵安裝同一個下載器（可續傳、驗 SHA-256），同一種背景＋輪詢。

_image_install: dict = {"status": None, "log": []}


class ImageModelsIn(BaseModel):
    accept_license: bool = False
    models_dir: str | None = None     # ComfyUI 沒開時，使用者用「瀏覽…」選的 models 資料夾
    only_upscale: bool = False        # 只裝成片放大模型（85 MB），不裝 Qwen 生圖（40 GB）


@machine_router.get("/image-models")
def image_models_info(models_dir: str | None = None):
    """畫面要顯示的：授權、每個檔多大、這台 ComfyUI 已經有哪些、目前下載進度。"""
    from ..machine import installer, stack

    url = settings_store.comfy_url()
    asked = installer.comfy_model_folders(url)
    # ComfyUI 沒開：用使用者選的 models 資料夾來看已經有哪些
    folders = asked or ({m["folder"]: str(pathlib.Path(models_dir) / m["folder"]) for m in stack.IMAGE_MODELS.values()}
                        if models_dir else {})
    files = []
    for name, m in stack.IMAGE_MODELS.items():
        have = installer.already_have(url, m["folder"], name, m["size"], folders.get(m["folder"]))
        files.append({"name": name, "folder": m["folder"], "gb": round(m["size"] / 2**30, 1), "present": have})
    return {"license": "Apache-2.0 (Qwen-Image) · BSD-3 (Real-ESRGAN)", "license_url": stack.IMAGE_LICENSE_URL, "comfy_answering": bool(asked),
            "comfy_url": url, "files": files, "total_gb": round(sum(m["size"] for m in stack.IMAGE_MODELS.values()) / 2**30, 1),
            "job": _image_install}


@machine_router.post("/image-models")
def image_models_install(body: ImageModelsIn):
    import threading

    from ..machine import installer

    if _image_install["status"] == "running":
        return _image_install
    if not body.accept_license:
        raise HTTPException(400, "accept the model license first")
    url = settings_store.comfy_url()
    _image_install.update(status="running", log=[])

    def run():
        try:
            for ev in installer.install_image_models(url, body.models_dir, body.only_upscale):
                if (_image_install["log"] and _image_install["log"][-1]["step"] == ev["step"]
                        and _image_install["log"][-1]["status"] == "running"):
                    _image_install["log"][-1] = ev
                else:
                    _image_install["log"].append(ev)
            _image_install["status"] = "done"
        except demo_mode.DemoBlocked:
            _image_install["status"] = "failed"
            raise
        except Exception as exc:  # noqa: BLE001 - 錯誤留在畫面上
            _image_install["log"].append({"step": "error", "status": "failed", "detail": f"{type(exc).__name__}: {str(exc)[:300]}"})
            _image_install["status"] = "failed"

    threading.Thread(target=run, daemon=True, name="greenlight-image-models").start()
    return _image_install
