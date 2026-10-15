"""本機生圖引擎：Qwen-Image 2512 走使用者自己的 ComfyUI（選角候選圖、之後的角色表／站位圖／首幀）。

為什麼是 2512 不是 2.1：2.1 的授權只准非商業（Qwen RESEARCH LICENSE 第 2 條）；
2512 是 Apache 2.0，使用者拿生出來的角色去做要賣的片也沒問題。

工作流照 ComfyUI 內建範本 `image_qwen_image_2512_with_2steps_lora.json` 換成 API 格式
（machine/qwen2512_t2i_api.json，節點編號跟範本一樣，好對照）：2 步加速 LoRA、cfg 1。

不搶顯示卡：選角是畫面上按了就等的同步請求，ComfyUI 在忙（使用者自己在生片）就不排隊苦等 ——
等一下下還不空就放棄生圖，這一輪退回文字版候選，並講明原因。
"""

from __future__ import annotations

import json
import pathlib
import random

import httpx

from ..services import settings_store

TEMPLATE_PATH = pathlib.Path(__file__).resolve().parent.parent / "machine" / "qwen2512_t2i_api.json"
MODEL_ID = "qwen-image-2512-fp8+turbo2step"

# 範本附的比例表（Qwen-Image 原生解析度，約 1.76 MP）
SIZES = {
    "1:1": (1328, 1328), "16:9": (1664, 928), "9:16": (928, 1664),
    "4:3": (1472, 1104), "3:4": (1104, 1472), "3:2": (1584, 1056), "2:3": (1056, 1584),
}
IDLE_WAIT_SECONDS = 90


class Unavailable(Exception):
    """這台機器現在生不了圖（ComfyUI 沒開、在忙、缺模型）—— 不是錯，是退回文字版的理由。"""


def _url() -> str:
    return settings_store.load().get("comfy_url", "http://127.0.0.1:8189")


_LOADER_FIELDS = ("unet_name", "clip_name", "vae_name", "lora_name")


def check_files(url: str, template: pathlib.Path) -> None:
    """問 ComfyUI 自己（/object_info）：這個工作流的載入節點要的模型檔，它看得到嗎？看不到就丟 Unavailable，講缺哪個。

    要哪些檔照工作流讀（載入節點的 *_name 欄位），不另外抄一份清單 —— 工作流換了，檢查自動跟著換。
    """
    wf = json.loads(template.read_text(encoding="utf-8"))
    missing = []
    for node in wf.values():
        for field in _LOADER_FIELDS:
            fname = node["inputs"].get(field)
            if not isinstance(fname, str):
                continue
            cls = node["class_type"]
            try:
                info = httpx.get(f"{url}/object_info/{cls}", timeout=10).json()
            except (httpx.HTTPError, ValueError) as exc:
                raise Unavailable(f"ComfyUI is not answering at {url}") from exc
            choices = info.get(cls, {}).get("input", {}).get("required", {}).get(field, [[]])[0]
            if fname not in choices:
                missing.append(fname)
    if missing:
        raise Unavailable("ComfyUI is missing model files: " + ", ".join(missing))


def check(url: str | None = None) -> None:
    """生圖（文字→圖）要的模型檔都在嗎。"""
    check_files(url or _url(), TEMPLATE_PATH)


EDIT_TEMPLATE = pathlib.Path(__file__).resolve().parent.parent / "machine" / "qwen_edit2511_api.json"
EDIT_MODEL_ID = "qwen-image-edit-2511-fp8mixed+lightning4step"


def edit(image_paths: list[str], instruction: str, aspect: str | None = None,
         prefix: str = "greenlight/edit", seed: int | None = None) -> tuple[bytes, str, dict]:
    """照參考圖改（Qwen-Image-Edit 2511，最多 3 張圖）：全身卡、角度卡、首幀都走這裡。

    為什麼真人版一定要「改」不能「重畫」：臉只生一次（選角鎖定的那張），之後每張圖都從它衍生；
    用文字重畫一次就是另一個人，真人的臉差一點觀眾就看得出來。

    image_paths：第 1 張是主圖（輸出預設照它的比例），第 2、3 張是額外參考（例如首幀＝場景＋兩個角色）。
       指示裡用 "image 1 / image 2 / image 3" 指它們（範本的寫法）。
    aspect：要換畫幅時給（全身卡 "3:4"）—— 改用空白畫布，參考圖只當條件，不再被主圖的比例綁住。
    """
    from . import comfy

    if not 1 <= len(image_paths) <= 3:
        raise ValueError("the edit model takes 1 to 3 images")
    url = _url()
    check_files(url, EDIT_TEMPLATE)
    names = comfy.upload_refs(url, image_paths)
    wf = json.loads(EDIT_TEMPLATE.read_text(encoding="utf-8"))
    wf["201"]["inputs"]["image"] = names[0]
    for i, name in enumerate(names[1:], 2):
        wf[f"20{i}"] = {"class_type": "LoadImage", "inputs": {"image": name}}
        for enc in ("151", "149"):           # 正向、反向兩個編碼器都要看到同樣的參考圖（範本就是這樣接的）
            wf[enc]["inputs"][f"image{i}"] = [f"20{i}", 0]
    wf["151"]["inputs"]["prompt"] = instruction
    if aspect:
        width, height = SIZES.get(aspect, SIZES["3:4"])
        wf["300"] = {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}}
        wf["169"]["inputs"]["latent_image"] = ["300", 0]
    wf["169"]["inputs"]["seed"] = int(seed if seed is not None else random.randint(1, 2**48))
    wf["123"]["inputs"]["filename_prefix"] = prefix
    try:
        data, info = comfy.render(url, wf, idle_timeout=IDLE_WAIT_SECONDS, want=(".png",))
    except TimeoutError as exc:
        raise Unavailable("ComfyUI is busy with other work") from exc
    info.update({"model": EDIT_MODEL_ID, "aspect": aspect or "from image 1"})
    return data, "image/png", info


def generate(prompt: str, aspect: str = "16:9", prefix: str = "greenlight/casting",
             seed: int | None = None) -> tuple[bytes, str, dict]:
    """生一張圖，回傳 (png bytes, mime, 資訊)。ComfyUI 不能用 → Unavailable。"""
    from . import comfy

    url = _url()
    check(url)
    width, height = SIZES.get(aspect, SIZES["16:9"])
    wf = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    wf["108"]["inputs"]["text"] = prompt
    wf["107"]["inputs"]["width"], wf["107"]["inputs"]["height"] = width, height
    wf["106"]["inputs"]["seed"] = int(seed if seed is not None else random.randint(1, 2**48))
    wf["123"]["inputs"]["filename_prefix"] = prefix
    try:
        data, info = comfy.render(url, wf, idle_timeout=IDLE_WAIT_SECONDS, want=(".png",))
    except TimeoutError as exc:
        raise Unavailable("ComfyUI is busy with other work") from exc
    info.update({"model": MODEL_ID, "width": width, "height": height})
    return data, "image/png", info
