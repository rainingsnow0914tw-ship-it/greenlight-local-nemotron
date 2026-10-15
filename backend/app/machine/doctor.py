"""機器檢查：這台電腦能不能跑 Greenlight Local 的影片管線？缺什麼、怎麼補？

一項一項查，每項回報 ok／warn（能用但沒驗證過）／block（不能用）／missing（缺，安裝器補得了），
附上看到的實際數字和下一步。資訊一律問平台本身，不猜：
- 顯示卡：nvidia-smi（名稱、顯存、驅動、compute capability、驅動支援的 CUDA 版本）
- ComfyUI：它自己的 /system_stats 和 /object_info（有哪些節點、每個載入器下拉選單裡有哪些檔）
- 要哪些節點和檔案：直接讀渲染模板（stack.template_needs），模板換了這裡自動跟著換

只讀，不改任何東西。用法：python -m local.doctor [ComfyUI 網址] [安裝磁碟]
"""

import json
import platform
import re
import shutil
import subprocess
import sys

import httpx

from . import stack


def _check(id_, status, label, detail, fix=""):
    return {"id": id_, "status": status, "label": label, "detail": detail, "fix": fix}


def _verify_tools():
    """生完查證與學語言模式要的三個本機工具：聽寫（faster-whisper＋模型）、日文讀音字典、中文拼音。
    打包版要在這裡自己報得出能不能用 —— 這幾個只有生了影片才會被叫到，壞了不會在啟動時發現（打包時補）。"""
    bad = []
    for mod in ("faster_whisper", "ctranslate2", "fugashi", "unidic_lite", "pypinyin"):
        try:
            __import__(mod)
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{mod}: {type(exc).__name__}")
    # 只 import 不夠：打包後字典檔路徑常常找不到 —— 實際查一個詞（顔色＝かおいろ）
    if not bad:
        try:
            from ..language.gates import _dict_readings

            if _dict_readings("顔色").get(0, ("", ""))[1] != "かおいろ":
                bad.append("japanese dictionary: lookup returned nothing")
        except Exception as exc:  # noqa: BLE001
            bad.append(f"japanese dictionary: {type(exc).__name__}: {str(exc)[:120]}")
    model = ""
    try:
        from huggingface_hub import try_to_load_from_cache

        hit = try_to_load_from_cache("Systran/faster-whisper-large-v3", "model.bin")
        model = "cached" if isinstance(hit, str) else "will download ~3 GB on first check"
    except Exception:  # noqa: BLE001
        model = "unknown"
    if bad:
        return _check("verify_tools", "warn", "Post-render check tools", "; ".join(bad),
                      "Checks after each shot (speech match, gibberish, timing) will be skipped until these load.")
    return _check("verify_tools", "ok", "Post-render check tools", f"speech recognition, kana and pinyin ready (model {model})")


def gpu() -> dict | None:
    """nvidia-smi 回報的第一張 NVIDIA 卡；沒有 nvidia-smi（沒有 NVIDIA 卡或沒裝驅動）回 None。"""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    out = subprocess.run([exe, "--query-gpu=name,memory.total,driver_version,compute_cap",
                          "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=20)
    if out.returncode or not out.stdout.strip():
        return None
    name, mem, driver, cap = [x.strip() for x in out.stdout.strip().splitlines()[0].split(",")]
    # 驅動支援到哪一版 CUDA：只有 nvidia-smi 的表頭有（「CUDA Version: 13.2」），查詢欄位裡沒有
    head = subprocess.run([exe], capture_output=True, text=True, timeout=20).stdout
    m = re.search(r"CUDA Version:\s*(\d+)\.(\d+)", head)
    return {"name": name, "vram_mb": int(float(mem)), "driver": driver, "compute_cap": float(cap),
            "cuda": (int(m.group(1)), int(m.group(2))) if m else None}


def ram_gb() -> float:
    """實體記憶體（GB）。Windows 用系統 API，不另外裝套件。"""
    if sys.platform == "win32":
        import ctypes

        class MEMSTAT(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = MEMSTAT()
        st.dwLength = ctypes.sizeof(MEMSTAT)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        return st.ullTotalPhys / 2**30
    import os
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30


def comfy(url: str) -> dict | None:
    """問 ComfyUI 自己：版本、有哪些節點、每個載入器下拉選單裡有哪些檔。連不上回 None。"""
    try:
        stats = httpx.get(f"{url}/system_stats", timeout=5).json()
        info = httpx.get(f"{url}/object_info", timeout=60).json()
    except (httpx.HTTPError, ValueError):
        return None
    return {"version": stats.get("system", {}).get("comfyui_version"), "object_info": info}


def run(comfy_url: str = "http://127.0.0.1:8188", install_drive: str = "C:\\") -> dict:
    """跑全部檢查。回傳 {"ready": 能不能生影片, "checks": [...], "gpu": ..., "comfy_version": ...}。"""
    checks = []

    # 作業系統：安裝器和 EXE 目前只做 Windows（作者的機器、ComfyUI 桌面版也是）
    if sys.platform == "win32" and platform.machine().endswith("64"):
        checks.append(_check("os", "ok", "Windows 64-bit", platform.platform()))
    else:
        checks.append(_check("os", "block", "Windows 64-bit", platform.platform(),
                             "The local video pipeline is Windows-only for now; the cloud version is in development."))

    # 顯示卡
    g = gpu()
    if not g:
        checks.append(_check("gpu", "block", "NVIDIA GPU", "nvidia-smi not found — no NVIDIA GPU or driver",
                             "An NVIDIA GPU with at least 24 GB of VRAM is required. The cloud version is in development."))
    else:
        vram_gb = g["vram_mb"] / 1024
        if g["vram_mb"] < stack.MIN_VRAM_MB:
            checks.append(_check("vram", "block", "GPU memory ≥ 24 GB", f"{g['name']}: {vram_gb:.1f} GB",
                                 f"Below the tested minimum (verified on {stack.VERIFIED_GPU}). The cloud version is in development."))
        else:
            checks.append(_check("vram", "ok", "GPU memory ≥ 24 GB", f"{g['name']}: {vram_gb:.1f} GB"))
        if g["compute_cap"] >= 12.0:
            checks.append(_check("gpu_gen", "ok", "GPU generation", f"compute capability {g['compute_cap']} (RTX 50 series) — the verified setup"))
        elif g["compute_cap"] >= 8.6:
            checks.append(_check("gpu_gen", "warn", "GPU generation", f"compute capability {g['compute_cap']}",
                                 "Not tested on this generation. The installer will use the int8 text encoder instead of FP4."))
        else:
            checks.append(_check("gpu_gen", "block", "GPU generation", f"compute capability {g['compute_cap']}",
                                 "Too old for the quantized H3 models."))
        if g["cuda"] and g["cuda"] >= stack.MIN_CUDA:
            checks.append(_check("driver", "ok", "Driver supports CUDA 13", f"driver {g['driver']}, CUDA {g['cuda'][0]}.{g['cuda'][1]}"))
        else:
            cuda = f"{g['cuda'][0]}.{g['cuda'][1]}" if g["cuda"] else "unknown"
            checks.append(_check("driver", "block", "Driver supports CUDA 13", f"driver {g['driver']}, CUDA {cuda}",
                                 "Update the NVIDIA driver (the H3 int8 kernels need PyTorch built for CUDA 13)."))

    ram = ram_gb()
    checks.append(_check("ram", "ok" if ram >= 32 else "warn", "System memory", f"{ram:.0f} GB",
                         "" if ram >= 32 else f"Not tested below 32 GB (verified with {stack.VERIFIED_RAM_GB} GB)."))
    checks.append(_verify_tools())

    free = shutil.disk_usage(install_drive).free / 2**30
    checks.append(_check("disk", "ok" if free >= stack.MIN_DISK_GB else "block", f"Free space on {install_drive}",
                         f"{free:.0f} GB free", "" if free >= stack.MIN_DISK_GB else
                         f"Models need about 42 GB; free at least {stack.MIN_DISK_GB} GB or pick another drive."))

    checks.append(_check("ffmpeg", "ok" if shutil.which("ffmpeg") else "missing", "ffmpeg (joins shots)",
                         shutil.which("ffmpeg") or "not found", "" if shutil.which("ffmpeg") else "The installer adds it."))

    # ComfyUI：有沒有在跑、模板要的節點和模型檔它有沒有
    c = comfy(comfy_url)
    if not c:
        checks.append(_check("comfyui", "missing", "ComfyUI", f"nothing answering at {comfy_url}",
                             "Install it from the app, or start your own ComfyUI and enter its address."))
    else:
        checks.append(_check("comfyui", "ok", "ComfyUI", f"version {c['version']} at {comfy_url}"))
        need = stack.template_needs()
        info = c["object_info"]
        lacking = [n for n in need["nodes"] if n not in info]
        checks.append(_check("nodes", "missing" if lacking else "ok", "Pipeline nodes",
                             f"missing: {', '.join(lacking)}" if lacking else f"all {len(need['nodes'])} nodes present",
                             "Update ComfyUI — the MiniMax H3 nodes are built in to recent versions." if lacking else ""))
        cap = g["compute_cap"] if g else 12.0
        for cls, fields in need["files"].items():
            for field, fname in fields:
                if cap < 12.0:
                    fname = stack.MODELS.get(fname, {}).get("alt_for_older_gpus", fname)
                choices = (((info.get(cls) or {}).get("input") or {}).get("required") or {}).get(field, [[]])[0]
                have = isinstance(choices, list) and fname in choices
                size = stack.MODELS.get(fname, {}).get("size", 0) / 2**30
                checks.append(_check(f"model:{fname}", "ok" if have else "missing", f"Model {fname}",
                                     "present" if have else f"not found ({size:.1f} GB to download)",
                                     "" if have else "The installer downloads it from Hugging Face and checks its SHA-256."))

    blocked = any(x["status"] == "block" for x in checks)
    missing = any(x["status"] == "missing" for x in checks)
    return {"ready": not blocked and not missing, "can_install": not blocked, "checks": checks,
            "gpu": g, "comfy_version": c["version"] if c else None}


if __name__ == "__main__":
    report = run(*sys.argv[1:3])
    mark = {"ok": "OK  ", "warn": "WARN", "block": "STOP", "missing": "NEED"}
    for x in report["checks"]:
        print(f"[{mark[x['status']]}] {x['label']}: {x['detail']}" + (f"\n        → {x['fix']}" if x["fix"] else ""))
    print("\nready to make video" if report["ready"] else
          ("missing pieces can be installed" if report["can_install"] else "this machine cannot run the local pipeline"))
    if "--json" in sys.argv:
        print(json.dumps(report, ensure_ascii=False, indent=1))
