"""本機影片引擎：把一鏡送進使用者自己的 ComfyUI（MiniMax H3 四步參考圖工作流），邊跑邊回報進度，做完把影片拿回來。

從 nemotron-director 的渲染節點（render_node/node.py）改來。差別：
- 節點是「往雲端領工作」；本機 app 直接送給本機的 ComfyUI，不經過任何雲端。
- 加了即時進度：ComfyUI 的 WebSocket 會推「第幾步／共幾步」與「現在跑到哪個節點」，
  畫面就能顯示「生成中 72%」—— 使用者要看得出程式在做什麼、做到哪一步。

不搶顯示卡：`wait_idle=True` 時，ComfyUI 有別的工作在跑就先等（使用者可能用同一台 ComfyUI 在做別的事）。
"""

from __future__ import annotations

import json
import pathlib
import time
import uuid
from typing import Callable

import httpx

TEMPLATE_PATH = pathlib.Path(__file__).resolve().parent.parent / "machine" / "h3_ref2v_4step_api.json"


def is_idle(url: str) -> bool:
    """ComfyUI 完全空著才算；連不上也當作不能用。"""
    try:
        q = httpx.get(f"{url}/queue", timeout=10).json()
        return not q["queue_running"] and not q["queue_pending"]
    except (httpx.HTTPError, ValueError, KeyError):
        return False


def upload_refs(url: str, paths: list[str]) -> list[str]:
    """把參考圖傳進 ComfyUI 的 input/greenlight/ 子資料夾，回傳 LoadImage 用的名稱。缺圖就停 —— 少一張圖生出來是另一個人。"""
    out = []
    for p in paths:
        path = pathlib.Path(p)
        if not path.exists():
            raise FileNotFoundError(f"reference image not found: {path}")
        with open(path, "rb") as fh:
            r = httpx.post(f"{url}/upload/image", files={"image": (path.name, fh)},
                           data={"subfolder": "greenlight", "type": "input", "overwrite": "true"}, timeout=60)
        r.raise_for_status()
        out.append(f"greenlight/{path.name}")
    return out


def build_workflow(prompt: str, ref_files: list[str], seed: int, duration_seconds: float,
                   megapixels: float, prefix: str, text_encoder: str | None = None) -> dict:
    """把參數塞進 H3 四步參考圖工作流。參考圖照順序接成 ref_image_0、1…，對上提示詞的 <Picture 1>、<Picture 2>…。

    text_encoder：非 RTX 50 系列的卡改用 int8 版文字編碼器（machine/stack.py 的 alt_for_older_gpus）。
    """
    p = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    p["140:131"]["inputs"]["prompt"] = prompt
    for i, fn in enumerate(ref_files):
        p[f"94{i}"] = {"class_type": "LoadImage", "inputs": {"image": fn}}
        p["140:131"]["inputs"][f"ref_images.ref_image_{i}"] = [f"94{i}", 0]
    p["140:129"]["inputs"]["noise_seed"] = int(seed)
    p["140:133"]["inputs"]["value"] = float(duration_seconds)
    p["115"]["inputs"]["megapixels"] = float(megapixels)
    p["92"]["inputs"]["filename_prefix"] = prefix
    if text_encoder:
        p["140:128"]["inputs"]["clip_name"] = text_encoder
    return p


def render(url: str, workflow: dict, on_progress: Callable[[dict], None] | None = None,
           wait_idle: bool = True, idle_timeout: float = 6 * 3600,
           want: tuple[str, ...] = (".mp4",)) -> tuple[bytes, dict]:
    """送出、等完成、取回成品。on_progress 會收到 {"stage": waiting|queued|running|done, "percent", "detail"}。

    want：要拿回哪種檔（影片 .mp4；生圖用 (".png",)）。影片跟圖共用這一個出口，等空檔、進度、錯誤處理只有一份。
    """
    say = on_progress or (lambda ev: None)
    t_wait = time.time()
    while wait_idle and not is_idle(url):
        if time.time() - t_wait > idle_timeout:
            raise TimeoutError("ComfyUI stayed busy; try again later")
        say({"stage": "waiting", "percent": 0, "detail": "ComfyUI is busy with other work — waiting for it to be free"})
        time.sleep(15)

    client_id = f"greenlight-{uuid.uuid4().hex[:8]}"
    t0 = time.time()
    r = httpx.post(f"{url}/prompt", json={"prompt": workflow, "client_id": client_id}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"ComfyUI refused the workflow: {r.text[:300]}")
    pid = r.json()["prompt_id"]
    say({"stage": "queued", "percent": 0, "detail": "sent to ComfyUI"})
    _follow(url, client_id, pid, say)

    h = httpx.get(f"{url}/history/{pid}", timeout=30).json().get(pid) or {}
    st = h.get("status", {})
    if st.get("status_str") == "error":
        msgs = [m for m in st.get("messages", []) if m[0] == "execution_error"]
        raise RuntimeError(f"ComfyUI error: {json.dumps(msgs)[:400]}")
    files = [f for o in h.get("outputs", {}).values() for f in o.get("images", []) + o.get("videos", [])
             if f.get("type") == "output" and f["filename"].lower().endswith(want)]
    if not files:
        raise RuntimeError(f"ComfyUI finished but produced no {'/'.join(want)}")
    f = files[0]
    video = httpx.get(f"{url}/view", params={"filename": f["filename"], "subfolder": f["subfolder"], "type": "output"},
                      timeout=120).content
    say({"stage": "done", "percent": 100, "detail": f"{round(time.time() - t0)} s"})
    return video, {"render_seconds": round(time.time() - t0), "comfy_file": f"{f['subfolder']}/{f['filename']}",
                   "bytes": len(video), "prompt_id": pid}


def _follow(url: str, client_id: str, pid: str, say: Callable[[dict], None]) -> None:
    """跟著 ComfyUI 的 WebSocket 回報進度，直到這個工作做完。WebSocket 斷了就退回輪詢 /history（不讓進度顯示壞掉擋住生成）。"""
    try:
        from websockets.sync.client import connect
    except ImportError:
        return _poll(url, pid, say)
    ws_url = url.replace("http://", "ws://").replace("https://", "wss://") + f"/ws?clientId={client_id}"
    node_count, done_nodes = 0, 0
    try:
        with connect(ws_url, open_timeout=10, max_size=None) as ws:
            while True:
                raw = ws.recv(timeout=600)
                if isinstance(raw, bytes):
                    continue                     # 預覽圖，不用
                msg = json.loads(raw)
                data = msg.get("data") or {}
                if data.get("prompt_id") not in (None, pid):
                    continue
                kind = msg.get("type")
                if kind == "execution_cached":
                    done_nodes += len(data.get("nodes") or [])
                elif kind == "executing":
                    if data.get("node") is None:
                        return                   # 這個工作整個做完
                    done_nodes += 1
                    node_count = max(node_count, done_nodes)
                    say({"stage": "running", "percent": None, "detail": f"step {done_nodes}"})
                elif kind == "progress":
                    v, m = data.get("value", 0), data.get("max", 1) or 1
                    say({"stage": "running", "percent": round(100 * v / m), "detail": f"sampling {v}/{m}"})
                elif kind == "execution_error":
                    return
    except Exception:  # noqa: BLE001 - 進度是附加的，斷線就改輪詢
        _poll(url, pid, say)


def _poll(url: str, pid: str, say: Callable[[dict], None]) -> None:
    while True:
        time.sleep(10)
        h = httpx.get(f"{url}/history/{pid}", timeout=30).json().get(pid)
        if h and (h.get("status", {}).get("completed") or h.get("status", {}).get("status_str") == "error"):
            return
        say({"stage": "running", "percent": None, "detail": "rendering"})
