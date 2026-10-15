"""安裝器：在使用者選的資料夾裡裝好一套 ComfyUI＋H3 模型，讓 Greenlight Local 自己有引擎可以用。

步驟（每一步都 yield 進度給 app 畫面）：
  1. 授權：MiniMax H3 社群授權排除某些地區 → 使用者沒按同意就不下載（授權是使用者跟 MiniMax 之間的事，我們只負責讓他看到）
  2. 下載官方 ComfyUI 攜帶版（GitHub 正式發佈，內含 Python 3.13＋PyTorch cu130 —— 本機裝機最大的坑 cu130 直接避開）
  3. 解壓縮
  4. 模型：已經有模型資料夾的（例如自己裝過 ComfyUI）就指過去、不重下；沒有就從 Hugging Face 下載，邊下邊算 SHA-256 驗證
  5. 啟動（帶 UTF-8：繁中 Windows 的 cp950 會讓印 ✓ 的節點把整個 ComfyUI 拖垮 —— 本機實測過）
  6. 用機器檢查（doctor）確認真的能用

可以中斷再續：下載用 HTTP Range 接著下，解壓過的、驗過的就跳過。
不碰任何既有的 ComfyUI：只寫進 target 資料夾，「指過去」的模型資料夾只讀。
"""

import hashlib
import os
import pathlib
import subprocess
import sys
import time

import httpx

from . import stack

COMFY_URL = "https://github.com/comfyanonymous/ComfyUI/releases/latest/download/ComfyUI_windows_portable_nvidia.7z"
DEFAULT_PORT = 8189          # 不用 8188：使用者自己的 ComfyUI 多半佔著 8188


def _download(url: str, dest: pathlib.Path, label: str, size: int = 0, sha256: str = ""):
    """下載到 dest（可續傳），一路 yield 進度；有校驗碼就驗，錯了刪掉重來的事交給使用者按「重試」。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    done_marker = dest.with_suffix(dest.suffix + ".verified")
    if dest.exists() and done_marker.exists():
        yield {"step": label, "status": "done", "detail": "already downloaded and verified"}
        return
    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=httpx.Timeout(30, read=120)) as r:
        if r.status_code == 416:            # 已經下完（.part 就是完整檔）
            pass
        else:
            r.raise_for_status()
            if have and r.status_code != 206:   # 伺服器不給續傳 → 從頭來
                have = 0
            total = size or (int(r.headers.get("content-length", 0)) + have)
            last = 0.0
            with open(part, "ab" if have else "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
                    have += len(chunk)
                    if time.time() - last > 1:
                        last = time.time()
                        yield {"step": label, "status": "running", "progress": have / total if total else None,
                               "detail": f"{have / 2**30:.2f} / {total / 2**30:.2f} GB"}
    if sha256:
        yield {"step": label, "status": "running", "detail": "checking SHA-256"}
        h = hashlib.sha256()
        with open(part, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 24), b""):
                h.update(chunk)
        if h.hexdigest() != sha256:
            part.unlink()
            raise ValueError(f"{dest.name}: checksum mismatch — the download was corrupted and has been removed; try again")
    part.replace(dest)
    done_marker.write_text("ok", encoding="utf-8")
    yield {"step": label, "status": "done", "detail": f"{dest.stat().st_size / 2**30:.2f} GB"}


def comfy_dir(target: pathlib.Path) -> pathlib.Path:
    """攜帶版解開後的根目錄（裡面有 python_embeded 和 ComfyUI）。"""
    return target / "ComfyUI_windows_portable"


def _extract(archive: pathlib.Path, target: pathlib.Path):
    root = comfy_dir(target)
    if (root / "python_embeded" / "python.exe").exists():
        yield {"step": "extract", "status": "done", "detail": "already extracted"}
        return
    yield {"step": "extract", "status": "running", "detail": "unpacking ComfyUI (a few minutes)"}
    # 用 Windows 內建的 tar（libarchive，Windows 10 起就有）：官方攜帶版用了 BCJ2 壓縮濾鏡，
    # Python 的 py7zr 不支援（實測 UnsupportedCompressionMethodError），又不想多下載一個 7-Zip
    tar = pathlib.Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
    out = subprocess.run([str(tar), "-xf", str(archive), "-C", str(target)], capture_output=True, text=True)
    if out.returncode:
        raise RuntimeError(f"unpacking failed: {out.stderr.strip()[:300]}")
    if not (root / "python_embeded" / "python.exe").exists():
        raise RuntimeError("unpacked, but python_embeded/python.exe is not where the official layout puts it")
    yield {"step": "extract", "status": "done", "detail": str(root)}


def _models(target: pathlib.Path, compute_cap: float, reuse_dir: str | None):
    """模型：有現成的資料夾就寫 extra_model_paths.yaml 指過去（只讀、不搬不複製）；缺的才下載。"""
    root = comfy_dir(target)
    models_root = root / "ComfyUI" / "models"
    if reuse_dir:
        reuse = pathlib.Path(reuse_dir)
        folders = sorted({m["folder"] for m in stack.MODELS.values()})
        yaml = "greenlight_reuse:\n" + f"  base_path: '{reuse}'\n" + "".join(f"  {f}: {f}/\n" for f in folders)
        (root / "ComfyUI" / "extra_model_paths.yaml").write_text(yaml, encoding="utf-8")
        yield {"step": "models", "status": "running", "detail": f"using existing models in {reuse} (read-only)"}
    for name in stack.models_for(compute_cap):
        m = stack.MODELS[name]
        if reuse_dir and (pathlib.Path(reuse_dir) / m["folder"] / name).exists():
            yield {"step": f"model {name}", "status": "done", "detail": "found in the existing models folder"}
            continue
        yield from _download(stack.hf_url(name), models_root / m["folder"] / name, f"model {name}", m["size"], m["sha256"])


def launch(target: pathlib.Path, port: int = DEFAULT_PORT) -> subprocess.Popen:
    """啟動裝好的 ComfyUI（背景、不開視窗），回傳行程。log 寫在 target/comfyui.log。"""
    root = comfy_dir(target)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    log = open(target / "comfyui.log", "a", encoding="utf-8")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.Popen([str(root / "python_embeded" / "python.exe"), "-s", "ComfyUI/main.py",
                             "--windows-standalone-build", "--port", str(port), "--disable-auto-launch"],
                            cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)


def _wait_up(port: int, seconds: int = 300):
    url = f"http://127.0.0.1:{port}"
    t0 = time.time()
    while time.time() - t0 < seconds:
        try:
            if httpx.get(f"{url}/system_stats", timeout=3).status_code == 200:
                yield {"step": "start", "status": "done", "detail": f"ComfyUI answering at {url}"}
                return
        except httpx.HTTPError:
            pass
        yield {"step": "start", "status": "running", "detail": f"starting ComfyUI ({int(time.time() - t0)} s)"}
        time.sleep(3)
    raise TimeoutError(f"ComfyUI did not answer at {url} within {seconds} s — see comfyui.log in the install folder")


def install(target: str, license_accepted: bool, compute_cap: float = 12.0, reuse_models: str | None = None,
            port: int = DEFAULT_PORT, start: bool = True):
    """整套安裝，一路 yield 進度事件；最後一個事件帶機器檢查的結果。

    start=False：只裝不啟動。顯示卡正在忙（例如使用者自己的 ComfyUI 在生片）時用 ——
    多開一個 ComfyUI 光初始化就要吃幾百 MB 顯存，顯存快滿的時候會把正在跑的那個擠爆（測試時顯存 23.6/24 GB）。
    """
    if not license_accepted:
        raise PermissionError("The MiniMax H3 license must be accepted before downloading the models: " + stack.LICENSE_URL)
    t = pathlib.Path(target)
    t.mkdir(parents=True, exist_ok=True)
    archive = t / "_downloads" / "ComfyUI_windows_portable_nvidia.7z"
    yield from _download(COMFY_URL, archive, "ComfyUI")
    yield from _extract(archive, t)
    yield from _models(t, compute_cap, reuse_models)
    if not start:
        yield {"step": "start", "status": "skipped", "detail": "installed; not started (GPU busy) — start it from the app later"}
        return
    launch(t, port)
    yield from _wait_up(port)
    from . import doctor
    report = doctor.run(f"http://127.0.0.1:{port}", str(t.anchor))
    yield {"step": "check", "status": "done" if report["ready"] else "failed", "detail": "machine check after install",
           "report": report}


def comfy_model_folders(comfy_url: str) -> dict[str, str]:
    """問使用者的 ComfyUI 自己：每一類模型放在哪個資料夾（`/internal/folder_paths`）。連不上回空的。

    為什麼問它不猜：桌面版、攜帶版、自己 git clone 的，模型資料夾都不一樣（桌面版還會共用一個資料夾）；
    ComfyUI 啟動時就算好了，問它最準。
    """
    try:
        r = httpx.get(f"{comfy_url}/internal/folder_paths", timeout=10)
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return {}
    # 每一類回傳路徑清單（v0.39 實測；舊版是 [路徑清單, 副檔名]，兩種都收）。
    # 挑名字跟類別一樣的那個：ComfyUI 把舊別名（diffusion_models 排第一的是 models\unet、text_encoders 是 models\clip）放前面，
    # 拿第一個雖然也讀得到，但檔案會落在使用者不會去找的資料夾。有外掛共用模型庫（桌面版）時它排在內建的前面，正好優先用它。
    out = {}
    for folder, value in data.items():
        paths = value[0] if isinstance(value, list) and value and isinstance(value[0], list) else value
        if isinstance(paths, str):
            paths = [paths]
        if isinstance(paths, list) and paths:
            named = [p for p in paths if pathlib.Path(p).name.lower() == folder.lower()]
            out[folder] = (named or paths)[0]
    return out


def install_image_models(comfy_url: str, models_dir: str | None = None):
    """把生圖模型包（stack.IMAGE_MODELS）下載進使用者的 ComfyUI，一路 yield 進度。

    放哪裡：先問 ComfyUI（comfy_model_folders）；ComfyUI 沒開時用使用者在畫面上選的模型資料夾
    （ComfyUI 的 models/，底下有 diffusion_models、loras…）。兩個都沒有就停，講清楚要使用者做什麼。
    已經有的檔（大小對）就跳過，不重下；下載可續傳，下完驗 SHA-256。
    下完 ComfyUI 不用重開：下拉選單每次都重新掃資料夾。
    """
    folders = comfy_model_folders(comfy_url)
    if folders:
        yield {"step": "where", "status": "done", "detail": f"asked ComfyUI at {comfy_url} where its models go"}
    elif models_dir:
        folders = {m["folder"]: str(pathlib.Path(models_dir) / m["folder"]) for m in stack.IMAGE_MODELS.values()}
        yield {"step": "where", "status": "done", "detail": f"ComfyUI is not running — using the folder you picked: {models_dir}"}
    else:
        raise RuntimeError("ComfyUI is not running, so it can't tell us where its models go — "
                           "start ComfyUI, or pick its models folder with Browse…")
    for name, m in stack.IMAGE_MODELS.items():
        dest = pathlib.Path(folders.get(m["folder"]) or pathlib.Path(models_dir or "") / m["folder"]) / name
        if dest.exists() and dest.stat().st_size == m["size"]:
            yield {"step": f"model {name}", "status": "done", "detail": f"already in {dest.parent}"}
            continue
        yield from _download(stack.image_hf_url(name), dest, f"model {name}", m["size"], m["sha256"])
    yield {"step": "done", "status": "done", "detail": "image models ready — ComfyUI picks them up without a restart"}


if __name__ == "__main__":
    # python -m local.installer D:\GreenlightLocal --accept-license [--reuse-models D:\path\to\models]
    args = sys.argv[1:]
    reuse = args[args.index("--reuse-models") + 1] if "--reuse-models" in args else None
    for ev in install(args[0], "--accept-license" in args, reuse_models=reuse, start="--no-start" not in args):
        print(f"{ev['step']:>40s}  {ev['status']:8s} {ev.get('detail', '')}", flush=True)
        if ev.get("report"):
            for c in ev["report"]["checks"]:
                print(f"{'':42s}[{c['status']}] {c['label']}: {c['detail']}")
