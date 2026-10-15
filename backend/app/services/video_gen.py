"""Veo 3.1 影片生成。

這一層走 REST 不走 SDK，理由是**已經實證過**：
    `scripts/verify_aspect_ratio.py` 與 `scripts/verify_extend.py`
    都是用這條路徑跑通的（含那個會回誤導性 404 的 x-goog-user-project 坑）。
    把實測過的呼叫原樣搬進 production，比換成 SDK 再賭一次穩。

    Veo 的生成是 long-running operation：送出拿到 operation name，
    再輪詢 fetchPredictOperation 直到 done。SDK 對 LRO 的包裝在不同版本
    行為不一，自己輪詢反而可控。

## 兩種呼叫

**生成**
    instances[].prompt（+ 可選 image 當首幀）
    parameters.{durationSeconds: 4|6|8, aspectRatio, sampleCount, generateAudio}

**續拍**
    instances[].video.{bytesBase64Encoded, mimeType}
    parameters.durationSeconds: 7
    → 實測 4.01 秒 + 7 = 11.05 秒，比例保持

續拍**接受任意 mp4 位元組，不限同一個 operation 的產物**。
   所以批次生成不必把整條鏈鎖在同一個 session 裡 ——
   中斷後可以從硬碟撈回片段繼續接，也可以事後才決定哪幾鏡要加長。

## 回應結構

實測是 inline base64（`bytesBase64Encoded` + `mimeType`）。
但**長片有可能改走 GCS `storageUri`**，所以兩種都處理 ——
只認一種的話，等到真的切換時會是一個「回 200 但拿不到影片」的謎題。
"""

from __future__ import annotations

import base64
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

import requests

from . import api_retry, config_loader, media_store

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Veo 只在區域 endpoint，不在 global（跟 Gemini 3.x 相反）。
_LOCATION = "us-central1"

# 生成的等待上限。**改這個數字前先確認誰在引用它。**
#
# 這個常數曾經整個 repo 沒有任何地方引用（改了也不生效）。
# 真正在生效的是 `_generate_omni(timeout_seconds=300)` 那個預設參數，
# 而它傳給 `requests.post(timeout=...)`。
#
# **`requests` 的 timeout 不是總時間預算。**
#    它是 connect timeout ＋ read timeout，而 read timeout 量的是
#    「兩次 socket 讀取之間的最大間隔」—— 只要對方連線還活著、
#    間歇有一點動靜，這個計時器就一直被重置，永遠不會觸發。
#
#    實測（同一鏡連按五次）：五次全部 504、
#    全部 899.999 秒，log 裡**只有 Cloud Run 自己的 maximum request
#    timeout**，沒有任何一行是我們印的 —— 逾時保護一次都沒觸發過。
#    後果不只是等 15 分鐘：504 是 Cloud Run 直接砍掉整個請求，
#    例外處理沒機會跑，clip 狀態存不下、ledger 一筆都沒有，
#    使用者與系統雙盲。
#
# 所以現在分成兩個數字，而且**都真的有人用**：
_GEN_TIMEOUT_SECONDS = 180
"""單次 HTTP 呼叫的軟性上限（給 requests 用）。

擋得住斷線／連不上這類問題，擋不住「連線活著但對方不回」—— 後者靠下面那個。
180 秒是實測正常值（log 上 39–55 秒）的三倍餘裕。
"""

_HARD_DEADLINE_SECONDS = 420
"""整個生成流程的**硬性**總預算，從進入 generate 那一刻算起，涵蓋所有重試。

兩邊夾出來的數字：
- **上限**：Cloud Run 的請求上限是 900 秒。被它砍掉的話例外處理沒機會跑、
  clip 狀態存不下、ledger 一筆都沒有 —— 必須在那之前主動放棄。
- **下限**：要容得下最壞的配額重試路徑（429 的等待表 25+50+70 秒，
  加上四次生成嘗試），否則正常的重試會被誤砍。

420 秒同時滿足兩邊，而且掛住時使用者 7 分鐘就會拿到一句看得懂的話，
不是轉圈十五分鐘再吃一個 504。
"""

# token 快取。每次呼叫都重新 refresh 會多出一次網路往返，
# 而批次生成會連續打十幾次。
_token_cache: dict[str, Any] = {"value": None, "expires_at": 0.0}
_TOKEN_TTL = 45 * 60


class VideoGenError(RuntimeError):
    """生成失敗。訊息保留 Google 原文，方便對照文件與配額頁。"""


class GenTimeout(VideoGenError):
    """硬性總預算用完了。

    獨立一個型別，是因為它跟其他 VideoGenError 有一個關鍵差異：
    **我們不知道對方那邊發生了什麼事。** 其他錯誤都是對方明確回了什麼
    （400、內容審查、配額），這個是對方什麼都沒回 ——
    影片可能沒生，也可能生好了只是我們沒收到（而那是要計費的）。
    錯誤訊息必須誠實反映這個不確定，不能承諾「沒扣到錢」。
    """


def _run_with_deadline(fn: Callable[[], T], seconds: float, label: str) -> T:
    """跑 fn，超過 seconds 就放棄等待並拋 GenTimeout。

    機制本體在 `services/deadline.py` —— route 層也要用同一套，
    所以抽出去了。兩份實作必然分岔，今天已經證明過一次
    （首頁與專案內頁那兩個學習迴路面板）。

    這裡只負責把它翻譯成 `GenTimeout`，讓上層既有的
    `except VideoGenError` 照樣接得到。
    """
    from . import deadline

    try:
        return deadline.run_with_deadline(fn, seconds, label)
    except deadline.DeadlineExceeded as exc:
        raise GenTimeout(
            f"影片模型超過 {int(seconds // 60)} 分鐘沒有回應（正常是 1–2 分鐘）。"
            f"這一鏡沒有拿到成品，可以直接再按一次重拍。"
            f"⚠️ 對方沒有回應，所以無法確認那邊有沒有已經生成並計費 —— "
            f"如果連續好幾鏡都這樣，通常是 Omni 服務端不穩，等幾分鐘再試。"
        ) from exc



@dataclass
class ClipResult:
    """一次生成的結果。"""

    uri: str
    """media_store 的 URI。生成失敗不會有 ClipResult，所以這裡一定有值。"""

    seconds: float
    """**實際**輸出秒數，從 mp4 解析出來的，不是請求的值。

    分開記「請求幾秒」與「實際幾秒」是有原因的：
       API 回 200 不代表它照做了（9:16 那次就是這樣驗出來的）。
       成本與後續接片都要用實際值。
    """

    width: int = 0
    height: int = 0
    size_bytes: int = 0
    requested_seconds: int = 0
    tier: str = ""
    model_id: str = ""
    extended_from: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def matched_request(self) -> bool:
        """實際長度跟請求的差在一秒內就算相符（編碼會有零點幾秒誤差）。"""
        if not self.requested_seconds:
            return True
        return abs(self.seconds - self.requested_seconds) <= 1.0


# ---------------------------------------------------------------------------
# 低階：認證與 endpoint
# ---------------------------------------------------------------------------


def _project_id() -> str:
    project = os.getenv("GCP_PROJECT_ID")
    if not project:
        raise VideoGenError(
            "缺少環境變數 GCP_PROJECT_ID。請在專案根目錄的 .env 裡設定"
            "（可以從 .env.example 複製）。"
        )
    return project


def _token(force: bool = False) -> str:
    """拿 access token。

    [修復記錄 - ]
    問題：連續生成到第 12 支時 HTTP 401 UNAUTHENTICATED，
          而且**按重拍也一樣失敗**。
    根因：快取寫死 45 分鐘（`_TOKEN_TTL`），那是猜的值不是憑證真正的
          到期時間。token 先過期時，快取仍然認為它有效 ——
          於是每一次重試都拿同一個死 token 再送一次，必然再 401。
          **重試機制遇上「壞掉的快取」會失效**：重試的前提是
          「下一次可能不一樣」，而快取保證了它一模一樣。
    修法：改用憑證自己的 `expiry`（留 5 分鐘餘裕），並讓呼叫端在
          收到 401 時用 force=True 強制換新的。

    Args:
        force: 忽略快取強制重新取得。401 之後一定要帶這個，
               否則重試沒有意義。
    """
    now = time.time()
    if not force and _token_cache["value"] and now < _token_cache["expires_at"]:
        return str(_token_cache["value"])

    import google.auth
    import google.auth.transport.requests

    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(google.auth.transport.requests.Request())

    # 憑證自己知道什麼時候到期 —— 用它的值，不要猜。
    # 留 5 分鐘餘裕：一支影片要跑 40–60 秒，剛好卡在到期邊緣會很難查。
    ttl = _TOKEN_TTL
    expiry = getattr(creds, "expiry", None)
    if expiry is not None:
        try:
            from datetime import datetime, timezone as _tz

            ref = datetime.now(_tz.utc) if expiry.tzinfo else datetime.utcnow()
            ttl = (expiry - ref).total_seconds() - 300
        except Exception:  # noqa: BLE001
            ttl = _TOKEN_TTL

    _token_cache["value"] = creds.token
    _token_cache["expires_at"] = now + max(60.0, min(float(ttl), float(_TOKEN_TTL)))
    return str(creds.token)


# ── Greenlight Local：用使用者自己的 Google 金鑰（Gemini API），不用任何人的雲端專案 ─────────────
#
# 原版走 Vertex（作者的 GCP 專案＋登入憑證）。本機版每個人用自己的金鑰，端點與認證都不同（照官方文件，讀的）：
#   Veo：POST  https://generativelanguage.googleapis.com/v1beta/models/{model}:predictLongRunning（x-goog-api-key）
#        輪詢 GET {BASE}/{operation name}；影片在 response.generateVideoResponse.generatedSamples[0].video.uri，帶金鑰下載
#   Omni：POST {BASE}/interactions（同一套 input／generation_config／response_format），影片 inline 在 steps[].content[]
# 模型名稱跟 Vertex 不一樣（Vertex 的 -001、preview 版 Omni），在 _api_model 對照。
_GEMINI = "https://generativelanguage.googleapis.com/v1beta"
_API_MODEL = {
    "veo-3.1-generate-001": "veo-3.1-generate-preview",
    "veo-3.1-fast-generate-001": "veo-3.1-fast-generate-preview",
    "veo-3.1-lite-generate-001": "veo-3.1-lite-generate-preview",
    "gemini-omni-flash-preview": "gemini-omni-1.1-flash",
}


def _api_key() -> str:
    from . import settings_store

    return settings_store.key("google")


def _use_api_key() -> bool:
    """有使用者自己的 Google 金鑰就用它；連 GCP_PROJECT_ID 都沒有（一般使用者）也走這條，缺金鑰時講清楚。"""
    return bool(_api_key()) or not os.getenv("GCP_PROJECT_ID")


def _api_model(model_id: str) -> str:
    return _API_MODEL.get(model_id, model_id)


def _api_headers() -> dict[str, str]:
    key = _api_key()
    if not key:
        raise VideoGenError("No Google API key — add yours in Settings to use the cloud video engine")
    return {"x-goog-api-key": key, "Content-Type": "application/json"}


def _headers(force_token: bool = False) -> dict[str, str]:
    if _use_api_key():
        return _api_headers()
    return _vertex_headers(force_token)


def _vertex_headers(force_token: bool = False) -> dict[str, str]:
    """`x-goog-user-project` 絕對不能省。

    少了它的錯誤訊息**極度誤導**：有些 endpoint 回 403，
    有些回 404「Publisher model was not found」—— 後者會讓人以為
    model ID 打錯，跑去查文件猜版本號，實際上 ID 完全正確。

    `gcloud auth application-default set-quota-project` 只寫進 ADC 檔，
    用 requests 打 REST 不會自動帶上。官方 SDK 會處理，但 Veo 的
    predictLongRunning 走 REST，要自己管。
    """
    return {
        "Authorization": f"Bearer {_token(force_token)}",
        "Content-Type": "application/json",
        "x-goog-user-project": _project_id(),
    }


def _endpoint(model_id: str, action: str) -> str:
    if _use_api_key():
        return f"{_GEMINI}/models/{_api_model(model_id)}:{action}"
    project = _project_id()
    return (
        f"https://{_LOCATION}-aiplatform.googleapis.com/v1/projects/{project}"
        f"/locations/{_LOCATION}/publishers/google/models/{model_id}:{action}"
    )


def _tier_config(tier: str) -> dict[str, Any]:
    return config_loader.veo_tier(tier)


# ---------------------------------------------------------------------------
# mp4 解析（零依賴，不用裝 ffmpeg）
# ---------------------------------------------------------------------------


def probe(data: bytes) -> dict[str, Any]:
    """從 mp4 位元組讀出寬高與長度。

    寬高在 tkhd 的最後 8 bytes（16.16 定點數）；
    長度在 mvhd 的 duration / timescale。

    自己解析而不依賴 ffmpeg —— 部署到 Cloud Run 時少一個系統套件，
    而且「量一下它真的幾秒」這件事必須是零摩擦的，
    否則就會被跳過，然後 9:16 那種「回 200 但沒照做」的問題就查不出來。
    """
    import struct

    info: dict[str, Any] = {"width": 0, "height": 0, "seconds": 0.0}

    def walk(buf: bytes, start: int, end: int) -> None:
        pos = start
        while pos + 8 <= end:
            size = struct.unpack(">I", buf[pos : pos + 4])[0]
            box_type = buf[pos + 4 : pos + 8]

            if size == 0:
                size = end - pos
            elif size == 1:
                if pos + 16 > end:
                    return
                size = struct.unpack(">Q", buf[pos + 8 : pos + 16])[0]

            if size < 8 or pos + size > end:
                return

            if box_type == b"tkhd" and not info["width"]:
                tail = buf[pos + size - 8 : pos + size]
                w = struct.unpack(">I", tail[0:4])[0] >> 16
                h = struct.unpack(">I", tail[4:8])[0] >> 16
                # 音軌的 tkhd 寬高是 0，要跳過繼續找視訊軌
                if w and h:
                    info["width"], info["height"] = w, h

            if box_type == b"mvhd" and not info["seconds"]:
                version = buf[pos + 8]
                if version == 0:
                    timescale = struct.unpack(">I", buf[pos + 20 : pos + 24])[0]
                    duration = struct.unpack(">I", buf[pos + 24 : pos + 28])[0]
                else:
                    timescale = struct.unpack(">I", buf[pos + 28 : pos + 32])[0]
                    duration = struct.unpack(">Q", buf[pos + 32 : pos + 40])[0]
                if timescale:
                    info["seconds"] = round(duration / timescale, 2)

            if box_type in (b"moov", b"trak", b"mdia", b"minf", b"stbl"):
                walk(buf, pos + 8, pos + size)

            pos += size

    walk(data, 0, len(data))
    return info


# ---------------------------------------------------------------------------
# LRO：送出與輪詢
# ---------------------------------------------------------------------------


def _submit(model_id: str, payload: dict[str, Any], label: str) -> str:
    def _do() -> str:
        resp = requests.post(
            _endpoint(model_id, "predictLongRunning"),
            headers=_headers(),
            json=payload,
            timeout=300,
        )
        # 401 一定要換 token 再試一次，不能只靠外層重試 ——
        #    外層重試會拿到同一個快取的死 token（
        #    按重拍，兩次都是 401）。
        if resp.status_code == 401:
            resp = requests.post(
                _endpoint(model_id, "predictLongRunning"),
                headers=_headers(force_token=True),
                json=payload,
                timeout=300,
            )
        if resp.status_code != 200:
            # 把狀態碼放進訊息裡，api_retry 才判斷得出這是不是可重試的錯誤
            raise VideoGenError(
                f"HTTP {resp.status_code} 送出失敗：{resp.text[:400]}"
            )
        name = resp.json().get("name")
        if not name:
            raise VideoGenError(f"回應裡沒有 operation name：{resp.text[:200]}")
        return str(name)

    # 走自適應調節（不是固定間隔）：Veo 的配額查不到，
    #    而批次生成會連續送十幾個 operation —— 那是最容易撞牆的地方。
    #    governor 依模型分開記憶，所以圖片被擋不會拖慢影片，反之亦然。
    return api_retry.governed(model_id, _do, label=f"veo:submit:{label}")


def _await(model_id: str, operation: str, timeout_seconds: int, label: str) -> dict[str, Any]:
    """輪詢到 done。

    輪詢本身不能用 api_retry 包：重試整段輪詢會從頭開始等，
       而 operation 已經在跑了。這裡自己處理暫時性錯誤 ——
       單次輪詢失敗就跳過這一輪，下一次再問。
    """
    deadline = time.time() + timeout_seconds
    consecutive_errors = 0

    while time.time() < deadline:
        time.sleep(12)
        try:
            if _use_api_key():
                # Gemini API：operation 名稱本身就是網址的路徑（官方文件：GET {BASE}/{operation_name}）
                poll = requests.get(f"{_GEMINI}/{operation}", headers=_headers(), timeout=90)
            else:
                poll = requests.post(
                    _endpoint(model_id, "fetchPredictOperation"),
                    headers=_headers(),
                    json={"operationName": operation},
                    timeout=90,
                )
        except Exception:  # noqa: BLE001
            consecutive_errors += 1
            if consecutive_errors >= 5:
                raise VideoGenError(f"{label}：連續五次輪詢失敗，放棄") from None
            continue

        if poll.status_code != 200:
            consecutive_errors += 1
            if consecutive_errors >= 5:
                raise VideoGenError(
                    f"{label}：輪詢持續回 HTTP {poll.status_code}：{poll.text[:200]}"
                )
            continue

        consecutive_errors = 0
        body = poll.json()
        if not body.get("done"):
            continue
        if "error" in body:
            raise VideoGenError(f"{label} 生成失敗：{body['error']}")
        return body

    raise VideoGenError(
        f"{label}：等了 {timeout_seconds} 秒還沒完成。"
        f"operation 仍在 Google 那邊跑，可以稍後用 operation name 再查一次。"
    )


def _extract_video(body: dict[str, Any]) -> tuple[bytes, str, list[str]]:
    """從 operation 結果挖出影片位元組。

    處理兩種回應：inline base64（實測）與 GCS URI（長片可能改走這種）。
    """
    warnings: list[str] = []
    response = body.get("response", {})
    # Gemini API（使用者自己的金鑰）：影片是一個網址，要帶金鑰下載（官方文件：curl -L -H x-goog-api-key）
    samples = (response.get("generateVideoResponse") or {}).get("generatedSamples") or []
    if samples:
        uri = ((samples[0] or {}).get("video") or {}).get("uri")
        if not uri:
            raise VideoGenError(f"回應裡的影片沒有網址：{str(samples[0])[:200]}")
        got = requests.get(uri, headers={"x-goog-api-key": _api_key()}, timeout=300, allow_redirects=True)
        if got.status_code != 200:
            raise VideoGenError(f"下載影片失敗 HTTP {got.status_code}：{got.text[:200]}")
        return got.content, got.headers.get("Content-Type", "video/mp4").split(";")[0], warnings
    videos = response.get("videos") or response.get("predictions") or []

    if not videos:
        raise VideoGenError(f"回應裡沒有影片。頂層 key：{list(response.keys())}")

    item = videos[0]
    mime = item.get("mimeType") or "video/mp4"

    b64 = item.get("bytesBase64Encoded") or item.get("video") or ""
    if isinstance(b64, str) and b64 and not b64.startswith("gs://"):
        return base64.b64decode(b64), mime, warnings

    gcs = item.get("gcsUri") or item.get("storageUri") or (b64 if isinstance(b64, str) else "")
    if isinstance(gcs, str) and gcs.startswith("gs://"):
        # 走 GCS 的話要下載回來。這條路徑目前沒有實測過 ——
        # 標成 warning，讓第一次真的走到這裡的人知道要驗一下。
        warnings.append(
            f"影片走 GCS 而不是 inline：{gcs}。這條路徑尚未實測，請確認內容正確。"
        )
        from google.cloud import storage

        bucket_name, _, blob_name = gcs[5:].partition("/")
        client = storage.Client(project=_project_id())
        data = client.bucket(bucket_name).blob(blob_name).download_as_bytes()
        return data, mime, warnings

    raise VideoGenError(f"認不得的回應格式，項目 key：{list(item.keys())}")


# ---------------------------------------------------------------------------
# 對外
# ---------------------------------------------------------------------------


def _interactions_endpoint() -> str:
    if _use_api_key():
        return f"{_GEMINI}/interactions"
    project = os.getenv("GCP_PROJECT_ID")
    if not project:
        raise VideoGenError("請設定 GCP_PROJECT_ID")
    return (
        f"https://aiplatform.googleapis.com/v1beta1/projects/{project}"
        f"/locations/global/interactions"
    )


def _generate_omni(
    project_id: str,
    clip_id: str,
    prompt: str,
    *,
    tier: str,
    tier_cfg: dict[str, Any],
    aspect_ratio: str = "16:9",
    reference_images: list[dict] | None = None,
    first_frame: bytes | None = None,
    first_frame_mime: str = "image/jpeg",
    seconds_override: int | None = None,
    timeout_seconds: int = _GEN_TIMEOUT_SECONDS,
    hard_deadline_seconds: float = _HARD_DEADLINE_SECONDS,
) -> ClipResult:
    """Gemini Omni Flash 生成（Interactions API）。**這會花錢**（10 秒 ≈ $1.01）。

    與 Veo 路徑的三個實測差異（，見 scripts/probes/verify_omni*.py）：
       1. **秒數不可控**，固定約 10 秒（generation_config 沒有任何我們找得到的
          可用參數 —— task/duration/aspect 全是 Unknown parameter）
       2. **比例靠提示詞文字**明講（有效，實測直式參考圖被 16:9 文字壓回橫式）；
          沒講的話會跟著參考圖的方向走
       3. 參考圖不受「8 秒、非接幀」限制 —— 每一鏡都能帶

    [修復記錄 - 晚]
    問題：首尾幀原本沒有接上。分鏡 agent 在
          32 鏡裡標了 24 個接點（跨場次 0 個，判得很準），Omni 路徑
          **整批忽略**，全部硬切生成。每一鏡的 warnings 都寫著
          「Omni 路徑暫不接首尾幀」，那句話真正的意思是
          「Pro 想好的 24 個接點被丟掉了」，但沒人這樣讀它。
    根因：當初寫的時候不確定 image input 會被當首幀還是當風格參考，
          就先擋著不用 —— 而那個「未驗證」從來沒有人回頭去驗。
    修法：$0.304 實測（拿 s029_02 的末幀當輸入生 3 秒，輸出影片的
          第一幀就是同一個畫面：人物、姿勢、背景、色溫全部延續）。
          確認 `task=image_to_video` 的圖就是第一幀，接上。
    坑：**接幀與參考圖互斥**，`video_config.task` 一次只能一個。
          接幀鏡不帶參考圖 —— 首幀本身已經帶著角色的臉。
          結構因此回到 Veo 時代那套：鏈頭帶參考圖鎖長相、
          鏈內後續鏡接末幀繼承。
    坑：「暫不支援 X」這種註釋要寫上**怎樣算驗過**，
          否則它會安靜地變成永久限制。
    """
    # 比例與秒數走正式 API 參數（平台 AI 情報 + $0 枚舉驗證）：
    #    response_format.{type:"video", aspect_ratio, duration} ——
    #    我們自己盲猜十幾個欄位名都沒中，platform AI 一句話點破位置。
    #    提示詞裡保留一句短的比例聲明當雙保險（preview 階段的防禦性寫法；
    #    參數實測會過 schema，服從度待第一批實拍確認後可拿掉）。
    orientation = "landscape" if aspect_ratio == "16:9" else "portrait"

    # [character N] 編號綁定（官方 prompting guide，平台 AI 情報）：
    #    提示詞開頭宣告第 N 張參考圖是誰，收尾要求各角色嚴格對齊自己的圖。
    #    官方上限 10 張；我們放寬到 6，夠鎖多角色同框。
    # 接幀優先於參考圖：task 一次只能一個，兩者互斥。
    #    優先接幀的理由跟 Veo 路徑一致 —— 連戲比單鏡身份更難事後補救，
    #    而接幀的首幀本身已經帶著角色長相。
    #    檔位沒宣告 supports_first_frame 就不走這條（設定驅動，不寫死）。
    use_first_frame = bool(first_frame) and bool(tier_cfg.get("supports_first_frame"))
    dropped_refs = bool(first_frame) and bool(reference_images) and use_first_frame

    all_refs = [] if use_first_frame else (reference_images or [])
    # 場景參考（scene=True，斷鏈鏡帶的上一鏡末幀）跟角色卡分開處理：
    # 它排在最後、不進 [character N] 編號 —— 編號綁定是「這張圖是誰」，
    # 場景圖裡的人臉不能被當成第 N 個角色。角色卡上限 6 張照舊，
    # 場景參考另計（Omni reference_to_video 上限 10 張，裝得下）。
    char_refs = [r for r in all_refs if not r.get("scene")][:6]
    scene_refs = [r for r in all_refs if r.get("scene")][:1]
    refs = char_refs + scene_refs
    binding = ""
    if char_refs:
        parts_b = []
        for i, r in enumerate(char_refs, 1):
            desc = r.get("tags") or "as shown in the reference image"
            parts_b.append(f"[character {i}] is {r.get('name', '')}: {desc}")
        binding = (
            "Character bindings — " + ". ".join(parts_b) + ". "
        )
    tail_bind = (
        " Each bound character must exactly match the face, hair, build and "
        "outfit shown in their numbered reference image."
        if char_refs
        else ""
    )
    if scene_refs:
        # 措辭刻意避開 reference / location / set 這類片場詞彙 ——
        # 實際觀察過成品畫面裡冒出攝影機，合理懷疑是這類詞
        # 讓模型把「拍片現場」畫進畫面。只描述房間本身。
        tail_bind += (
            " The final image shows the room where this shot happens — keep "
            "the same furniture, decor, lighting direction and colour grade "
            "as that image. Any person visible in that image is not part of "
            "this shot's cast, and its framing should not be copied."
        )
    # 接幀鏡要明講「從這張圖繼續」—— 提示詞裡的鏡別描述（例如
    # "Close-up of…"）可能跟首幀的構圖不一樣，不講的話模型會在
    # 「聽圖」和「聽字」之間自己選一個。
    continuity_line = (
        " This shot continues directly from the provided first frame: keep the "
        "same location, set dressing, lighting direction and colour grade, and "
        "keep the same character exactly as they appear in that frame."
        if use_first_frame
        else ""
    )
    full_prompt = (
        binding
        + prompt.strip()
        + f" The output video is {aspect_ratio} {orientation}."
        + tail_bind
        + continuity_line
    )

    inputs: list[dict[str, Any]] = [{"type": "text", "text": full_prompt}]
    if use_first_frame:
        inputs.append(
            {
                "type": "image",
                "data": base64.b64encode(first_frame).decode(),
                "mime_type": first_frame_mime,
            }
        )
    for r in refs:
        inputs.append(
            {
                "type": "image",
                "data": base64.b64encode(r["data"]).decode(),
                "mime_type": r["mime"],
            }
        )

    # 秒數可被覆寫：旁白鏡先合成 TTS、
    #    量出長度，再決定影片要生幾秒。沒覆寫就用檔位的預設長度。
    #    Omni 的 duration 實測可調 3–10 秒。
    fixed = int(seconds_override or tier_cfg.get("fixed_seconds", 10))
    fixed = max(3, min(10, fixed))
    model_id = tier_cfg.get("model_id", "gemini-omni-flash-preview")
    payload: dict[str, Any] = {
        # 使用者金鑰（Gemini API）的模型名稱跟 Vertex 不同：gemini-omni-1.1-flash
        "model": _api_model(model_id) if _use_api_key() else model_id,
        "input": inputs,
        "generation_config": {
            "video_config": {
                # task 明講不讓模型猜（$0 枚舉驗證過的合法值：
                # text_to_video / image_to_video / reference_to_video / edit / extend）
                "task": (
                    "image_to_video"
                    if use_first_frame
                    else ("reference_to_video" if refs else "text_to_video")
                ),
            }
        },
        "response_format": {
            "type": "video",
            "aspect_ratio": aspect_ratio,
            "duration": f"{fixed}s",
        },
    }

    def _do() -> dict[str, Any]:
        try:
            resp = requests.post(
                _interactions_endpoint(),
                headers=_headers(),
                json=payload,
                timeout=timeout_seconds,
            )
        except requests.Timeout as exc:
            # 翻成人話再拋：原本這裡讓 requests 的
            #    ReadTimeout 直接往上竄，而外層只會顯示一串英文堆疊。
            #
            # 原本這句寫「也不會扣到生成費用」—— 那是**沒有根據的安慰**。
            #    逾時的意思是我們沒收到回應，不是對方沒做事；
            #    Omni 完全可能已經生成並計費，只是結果沒送到我們手上。
            #    帳本會因此少記，而使用者被告知「沒扣錢」。誠實講不確定。
            raise GenTimeout(
                f"影片模型超過 {timeout_seconds // 60} 分鐘沒有回應（正常是 1–2 分鐘）。"
                f"這一鏡沒有拿到成品，可以直接再按一次重拍。"
                f"⚠️ 對方沒有回應，所以無法確認那邊有沒有已經生成並計費 —— "
                f"連續好幾鏡都這樣的話通常是 Omni 服務端不穩，等幾分鐘再試。"
            ) from exc
        # 401 換一個新 token 再試（第 12 支就死在這裡，
        #    而且重拍也一樣失敗 —— 快取的死 token 讓每次重試都一模一樣）。
        if resp.status_code == 401:
            resp = requests.post(
                _interactions_endpoint(),
                headers=_headers(force_token=True),
                json=payload,
                timeout=timeout_seconds,
            )
        if resp.status_code != 200:
            # 內容安全審查的拒絕要翻成人話（台詞
            # 「情感綁定協議…讓你產生依賴感」讓整鏡被拒）——
            # 原文「violates Google's Responsible AI practices」對編劇毫無指引。
            if "Responsible AI" in resp.text or "violates" in resp.text:
                raise VideoGenError(
                    "被 Google 內容安全審查擋下 —— 通常是這一鏡的台詞或描述"
                    "踩到敏感詞（重試同樣的內容會一樣被擋）。"
                    "可用「無聲重拍」：畫面照生、這句台詞改由字幕呈現。"
                    f"原始訊息：{resp.text[:200]}"
                )
            raise VideoGenError(
                f"HTTP {resp.status_code} Omni 送出失敗：{resp.text[:400]}"
            )
        return resp.json()

    # 硬性總預算從這裡開始算。
    #    `_do` 自己有 requests timeout，但那個擋不住「連線活著、對方不回」
    #    —— 五次 899.999 秒的 504 就是這樣來的。而且 governed 裡面還會
    #    對暫時性錯誤重試，單次上限乘上重試次數本來就可能超過 Cloud Run
    #    的 900 秒。**要管的是總和，所以把整包（含重試）一起框住。**
    deadline_at = time.monotonic() + hard_deadline_seconds

    def _remaining() -> float:
        return deadline_at - time.monotonic()

    body = _run_with_deadline(
        lambda: api_retry.governed(model_id, _do, label=clip_id, deadline=deadline_at),
        hard_deadline_seconds,
        clip_id,
    )

    # 官方範例是同步回 completed；保險起見支援輪詢
    while body.get("status") in ("in_progress", "pending", "processing", "running"):
        # 輪詢也吃同一份預算 —— 兩段各自計時的話，總和一樣會爆掉。
        if _remaining() <= 0:
            raise GenTimeout(
                f"影片模型超過 {int(hard_deadline_seconds // 60)} 分鐘還沒完成"
                f"（狀態停在 {body.get('status')}）。這一鏡沒有拿到成品，"
                f"可以直接再按一次重拍。"
            )
        time.sleep(min(10, max(1.0, _remaining())))
        resp = _run_with_deadline(
            lambda: requests.get(
                f"{_interactions_endpoint()}/{body.get('id')}",
                headers=_headers(),
                timeout=min(120, max(5.0, _remaining())),
            ),
            max(5.0, _remaining()),
            f"{clip_id}:poll",
        )
        body = resp.json()

    if body.get("status") not in (None, "completed"):
        raise VideoGenError(f"Omni 生成失敗：status={body.get('status')}")

    data: bytes | None = None
    for step in body.get("steps", []) or []:
        for c in step.get("content", []) or []:
            if c.get("type") == "video" and c.get("data"):
                data = base64.b64decode(c["data"])
                break
    if data is None:
        raise VideoGenError("Omni 回應裡沒有影片（steps 缺 video content）")

    info = probe(data)
    uri = media_store.save(project_id, clip_id, data, "video/mp4")

    result = ClipResult(
        uri=uri,
        seconds=float(info["seconds"]),
        width=int(info["width"]),
        height=int(info["height"]),
        size_bytes=len(data),
        requested_seconds=fixed,
        tier=tier,
        model_id=model_id,
        warnings=[],
    )
    if char_refs:
        result.warnings.append(
            f"✓ 這一鏡帶了 {len(char_refs)} 張角色參考圖鎖定長相。"
        )
    if scene_refs:
        result.warnings.append(
            "✓ 另帶上一鏡末幀當場景參考（只錨場景，不當角色）。"
        )
    if dropped_refs:
        # 成功無聲的功能跟壞掉的長得一樣（家規）—— 這裡少帶了東西，要說。
        result.warnings.append(
            "這一鏡接了前一鏡的末幀，所以沒有另外帶角色參考圖"
            "（兩者互斥）—— 長相由首幀本身鎖定。"
        )
    elif first_frame is not None and not use_first_frame:
        result.warnings.append(
            f"這個檔位（{tier}）沒有宣告支援接幀，這一鏡改用硬切。"
        )
    # 比例是文字求來的不是參數保證的 —— 量出來不對要大聲講
    expect_landscape = aspect_ratio == "16:9"
    got_landscape = result.width >= result.height
    if result.width and expect_landscape != got_landscape:
        result.warnings.append(
            f"要求 {aspect_ratio}，實際輸出 {result.width}×{result.height} —— "
            f"Omni 的比例只能用文字求，這一鏡沒求動，建議重拍。"
        )
    return result


def generate_clip(
    project_id: str,
    clip_id: str,
    prompt: str,
    *,
    tier: str = "lite_720p",
    aspect_ratio: str = "16:9",
    duration_seconds: int = 8,
    generate_audio: bool = True,
    timeout_seconds: int = _GEN_TIMEOUT_SECONDS,
    hard_deadline_seconds: float = _HARD_DEADLINE_SECONDS,
    first_frame: bytes | None = None,
    first_frame_mime: str = "image/jpeg",
    reference_images: list[dict] | None = None,
    seconds_override: int | None = None,
) -> ClipResult:
    """生一個鏡頭。**這會花錢。**

    Args:
        project_id: 決定檔案存在哪個子目錄
        clip_id: 檔名主體，通常是 shot_id
        duration_seconds: 只能是 4 / 6 / 8（Veo 硬限制，5 秒實測被拒絕）
        first_frame: 起始畫面（image-to-video）。通常是上一鏡的末幀 ——
            這是「接首尾幀」的實作。有了它，同一場戲的相鄰鏡頭才會
            延續色溫、角度、材質；沒有的話每一鏡都從零猜，
            「同一物件在下一鏡變成別的顏色」就是這樣來的。
        reference_images: 角色參考圖（asset reference），(bytes, mime) 列表。
            鎖**身份**不鎖構圖 —— 跟 first_frame 的分工：
            first_frame 決定「這一鏡從哪個畫面開始」，
            reference_images 決定「畫面裡的角色長什麼樣」。
            兩者互斥（實證：同時傳直接 400）。
            同時給的話以 first_frame 優先 —— 連戲比單鏡身份更難事後補救，
            而接幀的首幀本身已經帶著角色長相。

    Raises:
        VideoGenError: 參數不合法、送出失敗、生成失敗或逾時
    """
    # Demo 模式擋在最前面：影片是最貴的一步（約 $1/鏡），
    #    所有生成路徑都經過這裡，所以守住這一個入口就守住了錢。
    from . import demo_mode

    demo_mode.guard(
        "生成這一個鏡頭",
        model="Gemini Omni Flash",
        est_usd=float(_tier_config(tier).get("per_second_usd", 0.1))
        * float(seconds_override or duration_seconds or 10),
    )

    # ---- 引擎分派（Omni 當主引擎）----
    #
    # 檔位設定帶 engine: "omni" 就走 Interactions API；
    # 沒帶（Veo 各檔）維持原本的 predictLongRunning 路徑。
    # 分派放在 Veo 秒數驗證**之前** —— Omni 固定 10 秒，4/6/8 限制不適用。
    dispatch_cfg = _tier_config(tier)
    if dispatch_cfg.get("engine", "veo") == "omni":
        return _generate_omni(
            project_id,
            clip_id,
            prompt,
            tier=tier,
            tier_cfg=dispatch_cfg,
            aspect_ratio=aspect_ratio,
            reference_images=reference_images,
            first_frame=first_frame,
            first_frame_mime=first_frame_mime,
            seconds_override=seconds_override,
            timeout_seconds=timeout_seconds,
            hard_deadline_seconds=hard_deadline_seconds,
        )

    allowed = config_loader.pricing().get("veo", {}).get("clip_limits", {}).get(
        "allowed_seconds", [4, 6, 8]
    )
    if duration_seconds not in allowed:
        # 在 code 層擋死而不是靠 prompt 講清楚 —— 鐵律 6：
        # 能用架構解的不要用提示詞解。
        raise VideoGenError(
            f"durationSeconds 只能是 {allowed}，收到 {duration_seconds}。"
            f"要更長請用 extend_clip（每次 +7 秒）。"
        )

    tier_cfg = _tier_config(tier)
    model_id = tier_cfg.get("model_id", "veo-3.1-lite-generate-001")

    instance: dict[str, Any] = {"prompt": prompt}
    dropped_refs: str = ""
    attached_refs = 0
    if first_frame:
        # 形狀與 extend 同一套慣例：instances[].<欄位>.bytesBase64Encoded。
        #    extend 用 "video"，image-to-video 用 "image"。
        instance["image"] = {
            "bytesBase64Encoded": base64.b64encode(first_frame).decode(),
            "mimeType": first_frame_mime,
        }
        if reference_images:
            dropped_refs = "與接幀首幀不能同傳（實證 400）"
    elif reference_images:
        # asset reference：告訴模型「這個角色長這樣」，構圖與背景仍由提示詞決定。
        # 形狀由 scripts/verify_reference_image.py 確認。
        #
        # 兩個實測到的硬限制，都在 config 不寫死（pricing.yaml）：
        #    · Lite 不支援（supports_reference_images: false）
        #    · 只支援 8 秒（reference_images_allowed_seconds）
        #    不合條件就**略過參考圖照常生成**，不讓整鏡失敗 ——
        #    參考圖是加分項，跟抽幀失敗降級同一個原則。
        ref_seconds = config_loader.pricing().get("veo", {}).get(
            "clip_limits", {}
        ).get("reference_images_allowed_seconds", [8])
        if not tier_cfg.get("supports_reference_images", False):
            dropped_refs = f"檔位 {tier} 不支援角色參考圖"
        elif duration_seconds not in ref_seconds:
            dropped_refs = (
                f"角色參考圖只支援 {ref_seconds} 秒，這一鏡是 {duration_seconds} 秒"
            )
        else:
            instance["referenceImages"] = [
                {
                    "image": {
                        "bytesBase64Encoded": base64.b64encode(r["data"]).decode(),
                        "mimeType": r["mime"],
                    },
                    "referenceType": "asset",
                }
                for r in reference_images[:3]
            ]
            attached_refs = len(instance["referenceImages"])

    payload = {
        "instances": [instance],
        "parameters": {
            "durationSeconds": duration_seconds,
            "aspectRatio": aspect_ratio,
            "sampleCount": 1,
            "generateAudio": generate_audio,
        },
    }

    operation = _submit(model_id, payload, clip_id)
    body = _await(model_id, operation, timeout_seconds, clip_id)
    data, mime, warnings = _extract_video(body)

    info = probe(data)
    uri = media_store.save(project_id, clip_id, data, mime)

    result = ClipResult(
        uri=uri,
        seconds=float(info["seconds"]),
        width=int(info["width"]),
        height=int(info["height"]),
        size_bytes=len(data),
        requested_seconds=duration_seconds,
        tier=tier,
        model_id=model_id,
        extended_from="first_frame" if first_frame else None,
        warnings=warnings,
    )
    if dropped_refs:
        result.warnings.append(f"已略過角色參考圖：{dropped_refs}。")
    elif attached_refs:
        # 不是警告，是回條 —— 使用者需要能確認引導圖真的被送進去了。
        # 有帶沒帶要在 UI 上看得見，成功無聲的功能跟壞掉的功能長得一樣。
        result.warnings.append(f"✓ 這一鏡帶了 {attached_refs} 張角色參考圖鎖定長相。")

    # 回 200 不代表照做了。長度或比例對不上要明講，
    #    不要讓一個「其實不是 8 秒」的片段安靜地進入接片流程。
    if not result.matched_request:
        result.warnings.append(
            f"要求 {duration_seconds} 秒，實際輸出 {result.seconds} 秒。"
        )
    return result


def extend_clip(
    project_id: str,
    clip_id: str,
    source_video: bytes,
    prompt: str,
    *,
    tier: str = "lite_720p",
    seconds: int | None = None,
    generate_audio: bool = True,
    timeout_seconds: int = 300,
) -> ClipResult:
    """把一段影片往後續拍。**這會花錢。**

    （scripts/verify_extend.py）：4.01 秒 → 11.05 秒，
       比例保持，回應是 inline base64。

    `source_video` 可以是**任何 mp4 位元組**，不限同一個 operation 產出的。
       所以中斷後可以從 media_store 撈回片段繼續接，
       也可以等全部生完再回頭決定哪幾鏡要加長。

    Args:
        source_video: 要被續拍的那段影片，原始位元組
        seconds: 續多久。預設讀 pricing.yaml 的 seconds_per_extension（7）
    """
    veo_cfg = config_loader.pricing().get("veo", {}).get("extend", {})
    add_seconds = int(seconds or veo_cfg.get("seconds_per_extension", 7))

    tier_cfg = _tier_config(tier)
    model_id = tier_cfg.get("model_id", "veo-3.1-lite-generate-001")

    before = probe(source_video)

    payload = {
        "instances": [
            {
                "prompt": prompt,
                "video": {
                    "bytesBase64Encoded": base64.b64encode(source_video).decode(),
                    "mimeType": "video/mp4",
                },
            }
        ],
        "parameters": {
            "durationSeconds": add_seconds,
            "sampleCount": 1,
            "generateAudio": generate_audio,
        },
    }

    operation = _submit(model_id, payload, f"{clip_id}:extend")
    body = _await(model_id, operation, timeout_seconds, f"{clip_id}:extend")
    data, mime, warnings = _extract_video(body)

    info = probe(data)
    uri = media_store.save(project_id, clip_id, data, mime)

    result = ClipResult(
        uri=uri,
        seconds=float(info["seconds"]),
        width=int(info["width"]),
        height=int(info["height"]),
        size_bytes=len(data),
        requested_seconds=int(before["seconds"]) + add_seconds,
        tier=tier,
        model_id=model_id,
        extended_from=f"{before['seconds']}s",
        warnings=warnings,
    )

    # 續拍最危險的失敗模式不是報錯，是**長度沒有增加**：
    #    API 回 200、檔案也存下來了，但它只是重新生成了一段。
    #    成本照付、長鏡頭沒拿到，而且完全不會有錯誤訊息。
    if result.seconds <= float(before["seconds"]) + 1.0:
        result.warnings.append(
            f"續拍後長度沒有增加（{before['seconds']} → {result.seconds} 秒）——"
            f"它可能只是重新生成了一段，而不是接在後面。"
        )
    return result
