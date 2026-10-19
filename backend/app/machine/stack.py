"""Greenlight Local 跑影片要的整套東西：顯示卡門檻、模型檔從哪下載、多大、校驗碼。

為什麼單獨一個檔：機器檢查（doctor.py）、安裝器、說明文件都要這份清單 —— 一件事只能有一個家。
「要哪些節點、哪些模型檔名」不寫在這裡，直接讀渲染模板（render_node/h3_ref2v_4step_api.json）：
模板換了，檢查自動跟著換，不會兩邊對不上。這裡只放模板裡沒有的：下載來源、大小、校驗碼、門檻。

所有數字都是問平台問來的：
- 檔案、大小、sha256：Hugging Face API `api/models/Comfy-Org/MiniMax-H3/tree/main`
- 顯示卡：`nvidia-smi --query-gpu=name,memory.total,driver_version,compute_cap`
"""

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = pathlib.Path(__file__).resolve().parent / "h3_ref2v_4step_api.json"

HF_REPO = "Comfy-Org/MiniMax-H3"
LICENSE_URL = "https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE"
# 授權排除的地區（MiniMax H3 社群授權）：安裝前要讓使用者看到並按同意，不能默默裝
LICENSE_EXCLUDED = ["United States", "European Union", "United Kingdom", "South Korea"]

# 顯示卡門檻 —— 只寫「真的跑過」的，沒跑過的明講沒測過，不猜
VERIFIED_GPU = "NVIDIA GeForce RTX 5090 Laptop GPU (24 GB)"   # 作者的卡：整條管線（10 章動畫）都在這張上生
MIN_VRAM_MB = 24000          # 低於 24 GB 沒人跑過，擋
MIN_CUDA = (13, 0)           # PyTorch 要 cu130：cu128 會停用 int8_convrot 用的 CUDA 後端（本機裝機實測）
VERIFIED_RAM_GB = 64         # 作者的機器 64 GB；低於 32 GB 提醒「沒測過」
MIN_DISK_GB = 50             # 模型約 42 GB＋ComfyUI 本體與暫存

# 模型檔：檔名就是 ComfyUI 載入器下拉選單裡的名字（跟模板一致）
#   compute_cap 12.x（RTX 50 系列，有 FP4 硬體）用 nvfp4 的文字編碼器 —— 作者實際在用、驗證過
#   其他世代用 int8 版（Hugging Face 同一個倉庫提供）—— 沒有在別的卡上跑過，標「未驗證」
MODELS = {
    "minimax_h3_fl2va_pruned_int8_convrot.safetensors": {
        "folder": "diffusion_models", "size": 20970379616,
        "sha256": "e889202c41dafb67b10d67b97f0d8541508036a6090af23425a5c2615d03c47a"},
    "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors": {
        "folder": "loras", "size": 1956193000,
        "sha256": "5b9ab5ade15d0775676d01a907268a69a1468dc6033b3b0d3ded5502f3ebb84c"},
    "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors": {
        "folder": "text_encoders", "size": 15687142551,
        "sha256": "35a88d51044231fe332301d7a62aa81e3f2cba62febeb446e2c1e3e0ef76f2c6",
        "alt_for_older_gpus": "qwen3vl_32b_minimax_h3_int8_convrot.safetensors"},
    "qwen3vl_32b_minimax_h3_int8_convrot.safetensors": {
        "folder": "text_encoders", "size": 27141342152, "unverified": True,
        "sha256": "bc2ced0fbea64757fa9acddccfc0b3f4819d1dcf1da6c124d690d368be283923"},
    "minimax_h3_video_vae_fp16.safetensors": {
        "folder": "vae", "size": 5207808496,
        "sha256": "7c1f131492e7eddacaac9069a61b81bdd39de5cc96561e677c5eab1cdce5e522"},
    "minimax_h3_audio_vae_fp32.safetensors": {
        "folder": "vae", "size": 605254808,
        "sha256": "8e505d95dd1561d47abd43d4238fd40d9bb1ae9e147ed0a4cba778d76ae4db48"},
}


def hf_url(name: str) -> str:
    """模型檔在 Hugging Face 的下載網址。"""
    return f"https://huggingface.co/{HF_REPO}/resolve/main/{MODELS[name]['folder']}/{name}"


def template_needs() -> dict:
    """從渲染模板讀出：要哪些節點類別、每個載入器要哪個檔名。回傳 {"nodes": [...], "files": {node類別: [(欄位, 檔名)]}}。"""
    wf = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    nodes, files = set(), {}
    for n in wf.values():
        nodes.add(n["class_type"])
        for field, value in n["inputs"].items():
            if isinstance(value, str) and value.endswith(".safetensors"):
                files.setdefault(n["class_type"], []).append((field, value))
    return {"nodes": sorted(nodes), "files": files}


def models_for(compute_cap: float) -> list:
    """這張卡要下載哪些模型檔：50 系列用作者驗證過的組合；其他世代把文字編碼器換成 int8 版。"""
    needed = [f for loader in template_needs()["files"].values() for _, f in loader]
    if compute_cap < 12.0:
        needed = [MODELS[f].get("alt_for_older_gpus", f) for f in needed]
    return needed


# ── 生圖模型包（選角候選圖、之後的角色表／站位圖／首幀）─────────────────────────
#
# Qwen-Image 2512：Apache 2.0（商業可用）。不用更新的 2.1 —— 2.1 是 Qwen RESEARCH LICENSE，只准非商業（讀過全文第 2 條）。
# 檔案照 ComfyUI 內建範本 image_qwen_image_2512_with_2steps_lora.json；大小、sha256 問 Hugging Face（X-Linked-Size／X-Linked-ETag）。
# 檔名就是工作流 machine/qwen2512_t2i_api.json 載入節點裡的名字。
IMAGE_LICENSE_URL = "https://huggingface.co/Qwen/Qwen-Image-2512"
IMAGE_MODELS = {
    "qwen_image_2512_fp8_e4m3fn.safetensors": {
        "folder": "diffusion_models", "size": 20430679144,
        "repo": "Comfy-Org/Qwen-Image_ComfyUI", "path": "split_files/diffusion_models",
        "sha256": "5dc80554d5d83390046a2f4a94ece06afb7700bf7b0aaf8bde9769793875876b"},
    "qwen_2.5_vl_7b_fp8_scaled.safetensors": {
        "folder": "text_encoders", "size": 9384670680,
        "repo": "Comfy-Org/Qwen-Image_ComfyUI", "path": "split_files/text_encoders",
        "sha256": "cb5636d852a0ea6a9075ab1bef496c0db7aef13c02350571e388aea959c5c0b4"},
    "qwen_image_vae.safetensors": {
        "folder": "vae", "size": 253806246,
        "repo": "Comfy-Org/Qwen-Image_ComfyUI", "path": "split_files/vae",
        "sha256": "a70580f0213e67967ee9c95f05bb400e8fb08307e017a924bf3441223e023d1f"},
    # 2 步加速 LoRA（Wuli-art，Apache 2.0）：沒有它要跑 20～50 步
    "Wuli-Qwen-Image-2512-Turbo-LoRA-2steps-V1.0-bf16.safetensors": {
        "folder": "loras", "size": 2359534464,
        "repo": "Wuli-art/Qwen-Image-2512-Turbo-LoRA-2-Steps", "path": "",
        "sha256": "76b5525a2babb2d96ca1fc37f5616f755cec51e7f71dac59294412f968565193"},
    # 照參考圖改（Qwen-Image-Edit-2511，Apache 2.0）：全身卡、角度卡、首幀都從「鎖定的那張臉」改出來，不重新畫臉。
    # 真人版臉一跑觀眾馬上看得出來 —— 所以真人的每一張圖都要從同一張臉衍生（範本 image_qwen_image_edit_2511.json，最多吃 3 張圖）。
    # 文字編碼器、VAE 跟上面共用，只多主模型＋4 步加速 LoRA。
    "qwen_image_edit_2511_fp8mixed.safetensors": {
        "folder": "diffusion_models", "size": 20533762817,
        "repo": "Comfy-Org/Qwen-Image-Edit_ComfyUI", "path": "split_files/diffusion_models",
        "sha256": "c9fdc158e46d3b61ef75f21ae866ca2fe808bf4a53643120d1c1e87c19280a4e"},
    "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors": {
        "folder": "loras", "size": 849608296,
        "repo": "lightx2v/Qwen-Image-Edit-2511-Lightning", "path": "",
        "sha256": "22226e8d05d354bb356627d428809f5afd7819399b077238a2b70a82883a904f"},
    # 放大模型（Real-ESRGAN，BSD-3）：接幀前修上一鏡的末幀（不修每接一次糊一點）、成片放大到 1080p。
    # 官方 GitHub release；大小問 GitHub（Content-Length）、sha256 量作者機器上的檔（兩邊大小一致）。
    "RealESRGAN_x4plus.pth": {
        "folder": "upscale_models", "size": 67040989,
        "url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
        "sha256": "4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1"},
    "RealESRGAN_x4plus_anime_6B.pth": {
        "folder": "upscale_models", "size": 17938799,
        "url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth",
        "sha256": "f872d837d3c90ed2e05227bed711af5671a6fd1c9f7d7e91c911a61f155e99da"},
}


def image_hf_url(name: str) -> str:
    """生圖模型檔的下載網址。"""
    m = IMAGE_MODELS[name]
    if m.get("url"):
        return m["url"]
    return f"https://huggingface.co/{m['repo']}/resolve/main/{m['path'] + '/' if m['path'] else ''}{name}"
