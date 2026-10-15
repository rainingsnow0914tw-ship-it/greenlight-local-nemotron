"""生成媒體的存放層（圖片與影片）。

設計理由：
    候選圖、試拍影片、批次片段、成片都是二進位檔案，共通需求是
    開發期存本機、正式環境存雲端，而呼叫端不該關心差別。
    存放邏輯與檔案類型無關，只有副檔名對照表需要補，所以圖與影片共用同一層，
    避免出現兩份各自演化的原子寫入策略。

    這一層只暴露三件事：存進去拿到一個 URI、拿 URI 讀回來、
    拿 URI 問路徑。切換儲存後端時只改這個檔，agent 跟 route 不用動。

URI 格式：
    `local://{project_id}/{filename}`       開發期，檔案在 ~/.greenlight/media/
    `gs://{bucket}/{project_id}/{filename}` 正式環境（設了 GCS_BUCKET 就走這條）

為什麼正式環境非走 GCS 不可：
    Cloud Run 的容器磁碟是暫時的，執行個體收掉就沒了，
    而且多個執行個體之間不共用。存本機的話，下一個請求打到另一個執行個體
    就找不到檔案，且不會報錯，只會顯示「檔案遺失」。

舊的 `local://` URI 必須照樣讀得到：
    既有專案狀態裡存的是 local URI。load() 依 scheme 分派，兩種格式並存，
    不做資料遷移（遷移腳本本身就是一個會出錯而且只跑一次的東西）。

為什麼不直接把 base64 存進 project JSON：
    一張候選圖約 1.2MB，base64 之後 1.6MB。三張一輪、抽十輪 = 48MB
    塞進單一 JSON 檔，每次存檔都要整份重寫。而且 Firestore 單文件上限 1MB，
    正式環境直接爆掉。二進位資料一律走檔案／物件儲存，狀態檔只存 URI。
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

# 副檔名對照。模型回什麼 mimeType 就存什麼副檔名 ——
# 不強制轉檔（轉檔要裝影像函式庫，而且會掉品質）。
_EXT_BY_MIME = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "video/mp4": "mp4",
    "video/webm": "webm",
}

_IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "gif"}
_VIDEO_EXTS = {"mp4", "webm", "mov", "m4v"}

_LOCAL_SCHEME = "local://"
_GCS_SCHEME = "gs://"


def bucket_name() -> str:
    """正式環境的 GCS bucket。沒設就是本機模式。

    用「有沒有設環境變數」當開關，而不是另外弄一個 GREENLIGHT_MEDIA_BACKEND
    —— 少一個可以設錯的旋鈕。設了 bucket 卻想存本機是沒有意義的組合。
    """
    return os.getenv("GCS_BUCKET", "").strip()


def _gcs_bucket():
    """取得 GCS bucket 物件。延遲 import —— 本機開發不需要這個依賴。"""
    from google.cloud import storage

    return storage.Client(project=os.getenv("GCP_PROJECT_ID")).bucket(bucket_name())


def root() -> Path:
    """媒體根目錄。

    跟 project_store 用同一個 `~/.greenlight` 家目錄，方便整包備份或清空。
    可用 GREENLIGHT_MEDIA_ROOT 覆寫（測試時指到暫存目錄，不污染真實資料）。
    """
    override = os.getenv("GREENLIGHT_MEDIA_ROOT") or os.getenv("GREENLIGHT_IMAGE_ROOT")
    if override:
        return Path(override)
    from .env_boot import data_home
    return data_home() / "media"


def ext_for(mime_type: str) -> str:
    """mimeType → 副檔名。認不得的一律當 png。

    認不得就當 png 是安全的：Gemini image 目前實測只回 PNG，
    真的回了別的格式，副檔名錯不影響前端顯示（回應時會帶正確的 Content-Type）。
    """
    return _EXT_BY_MIME.get(mime_type.lower(), "png")


def save(project_id: str, name: str, data: bytes, mime_type: str = "image/png") -> str:
    """存一個檔案，回傳 URI。

    Args:
        project_id: 專案 ID，決定子目錄 —— 刪專案時整個目錄一起刪掉就乾淨了
        name: 檔名主體（通常是 candidate_id），不含副檔名
        data: 原始位元組（不是 base64 字串）
        mime_type: 模型回報的 mimeType

    Returns:
        `local://{project_id}/{name}.{ext}`

    Demo 模式在這裡擋下：這是資料安全問題，不是省錢問題。
    demo 專案是正式專案的複本，沿用同一個 project_id，而媒體路徑是
    `{project_id}/{name}`。若讓 demo 寫入，接片或燒字幕會蓋掉正式專案的成品，
    而那兩個操作不呼叫 AI，不會被 genai_client 那層攔住。
    守住這個唯一的寫入出口就守住了全部；demo 的成品本來就已存在，直接播放即可。
    """
    from . import demo_mode

    demo_mode.guard(f"產生新檔案（{name}）")

    ext = ext_for(mime_type)
    filename = f"{name}.{ext}"

    if bucket_name():
        # GCS 的寫入本身就是原子的（物件要嘛整個出現、要嘛不出現），
        # 不需要本機那套 tmp + rename。
        blob = _gcs_bucket().blob(f"{project_id}/{filename}")
        blob.upload_from_string(data, content_type=mime_type)
        return f"{_GCS_SCHEME}{bucket_name()}/{project_id}/{filename}"

    folder = root() / project_id
    folder.mkdir(parents=True, exist_ok=True)

    # 先寫暫存再改名 —— 跟 project_store 同一套原子寫入策略。
    # 生成中途當掉時，不會留下一個「看起來存在但只有一半」的圖檔，
    # 那種半截檔案在 UI 上會變成破圖，而且很難查。
    tmp = folder / f".{filename}.tmp"
    tmp.write_bytes(data)
    tmp.replace(folder / filename)

    return f"{_LOCAL_SCHEME}{project_id}/{filename}"


_SEGMENT_RE = re.compile(r"[A-Za-z0-9._-]{1,128}")


def safe_segment(seg: str) -> bool:
    """URI／路徑裡的一段（project_id 或檔名）合不合法：只准英數、點、底線、連字號，
    而且不能是 `.`／`..`。

    `path_for()` 直接 `root() / 段落`，本機模式下 `..` 當 project_id 可以指到根目錄外。
    雲端走 GCS 沒事，但本機開發也不該留這個洞。所有把 URI 轉成路徑的地方一律先過這裡。
    """
    return bool(_SEGMENT_RE.fullmatch(seg)) and seg not in (".", "..")


def path_for(uri: str) -> Path | None:
    """URI → 本機路徑。不是本機 URI 就回 None（呼叫端該走雲端分支）。"""
    if not uri.startswith(_LOCAL_SCHEME):
        return None
    rel = uri[len(_LOCAL_SCHEME) :]
    parts = [seg for seg in rel.split("/") if seg]
    if not parts or not all(safe_segment(seg) for seg in parts):
        return None
    base = root().resolve()
    target = (base.joinpath(*parts)).resolve()
    if base != target and base not in target.parents:
        return None  # 解析後跑到根目錄外 —— 不管怎麼繞都不給
    return target


def load(uri: str) -> tuple[bytes, str] | None:
    """讀回一個檔案，回傳 (位元組, mimeType)。找不到回 None。

    找不到不拋例外 —— 檔案可能被手動清掉，或是從別台機器複製過來的
    專案狀態檔帶著本機沒有的 URI。這種情況 UI 該顯示「檔案遺失」
    而不是整頁 500。

    依 scheme 分派，兩種格式並存。舊專案的 `local://` URI 在雲端讀不到
    是預期行為（那些檔案在開發機上），回 None 讓 UI 顯示遺失，
    比拋例外好：一支舊影片讀不到不該讓整個專案打不開。
    """
    if uri.startswith(_GCS_SCHEME):
        return _load_gcs(uri)

    path = path_for(uri)
    if path is None or not path.exists():
        return None

    ext = path.suffix.lstrip(".").lower()
    mime = next(
        (m for m, e in _EXT_BY_MIME.items() if e == ext),
        "image/png",
    )
    return path.read_bytes(), mime


def _load_gcs(uri: str) -> tuple[bytes, str] | None:
    """讀 `gs://bucket/path` 回來。讀不到回 None。"""
    from google.cloud import storage

    rest = uri[len(_GCS_SCHEME) :]
    bucket, _, blob_path = rest.partition("/")
    if not blob_path:
        return None

    try:
        blob = storage.Client(project=os.getenv("GCP_PROJECT_ID")).bucket(bucket).blob(
            blob_path
        )
        if not blob.exists():
            return None
        data = blob.download_as_bytes()
    except Exception:  # noqa: BLE001 - 讀不到就是讀不到，交給呼叫端顯示遺失
        return None

    ext = blob_path.rsplit(".", 1)[-1].lower()
    mime = next((m for m, e in _EXT_BY_MIME.items() if e == ext), "image/png")
    return data, mime


def project_usage(project_id: str) -> tuple[int, int]:
    """這個專案佔了多少媒體空間。回 (檔案數, 總位元組)。**不刪任何東西。**

    給刪除前的預覽用 —— 使用者按下不可逆的按鈕之前，
    有權知道會清掉多少東西。
    """
    items = list_media(project_id)
    return len(items), sum(i.get("size_bytes", 0) for i in items)


def delete_project(project_id: str) -> int:
    """刪掉一個專案的所有媒體檔，回傳刪除數量。

    Demo 模式在這裡也擋：route 層雖有擋，媒體是共用的，出口再守一次。

    專案被刪除時要一起清，否則抽了幾十輪的候選圖與生成失敗的
    影片片段會永遠佔著空間，而且沒有任何入口能找到它們。

    本機與 GCS 兩邊都要刪，回傳實際刪除數：只刪本機的話，雲端的圖與影片
    會變成沒有任何入口找得到、卻一直計費的孤兒。
    """
    from . import demo_mode
    demo_mode.guard(f"刪除專案媒體（{project_id}）")
    count = 0

    folder = root() / project_id
    if folder.exists():
        for item in folder.iterdir():
            if item.is_file():
                item.unlink()
                count += 1
        try:
            folder.rmdir()
        except OSError:
            pass  # 還有子目錄就留著，不是致命問題

    bucket = bucket_name()
    if bucket:
        try:
            from google.cloud import storage

            client = storage.Client(project=os.getenv("GCP_PROJECT_ID"))
            blobs = list(client.list_blobs(bucket, prefix=f"{project_id}/"))
            for blob in blobs:
                blob.delete()
                count += 1
        except Exception:  # noqa: BLE001 - 刪不掉不該讓整個刪除失敗
            pass

    return count


def find(name: str, project_id: str | None = None) -> str | None:
    """用**檔名主體**（不含副檔名）找出媒體 URI。找不到回 None。

    為什麼要有這個函式：
        多個 route 都要拿一個 id（candidate_id / clip_id）去找對應的檔案。
        若各自用 `root().glob()` 掃本機磁碟，在 Cloud Run 上那個目錄永遠是空的
        （檔案全在 GCS），雲端就會靜默失敗而本機測不出來。
        把「找檔案」收成一個入口，兩種後端都在這裡處理。

    新的 route 要找檔案一律走這裡，不要自己 glob 本機目錄。

    Args:
        name: 檔名主體，例如 `9b4f25-r1-1` 或 `s001_01`。不含副檔名。
        project_id: 限定在某個專案裡找。None 代表跨專案搜尋
            （candidate_id 全域唯一，但跨專案掃比較慢）。

    Returns:
        `local://…` 或 `gs://…`；找不到回 None。
    """
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    if project_id is not None and not safe_segment(project_id):
        return None

    # 本機優先 —— 開發時檔案就在磁碟上，不必打網路。
    root_dir = root()
    folders = (
        [root_dir / project_id]
        if project_id
        else ([f for f in root_dir.iterdir() if f.is_dir()] if root_dir.exists() else [])
    )
    for folder in folders:
        if not folder.is_dir():
            continue
        for item in folder.glob(f"{name}.*"):
            if item.is_file():
                return f"{_LOCAL_SCHEME}{folder.name}/{item.name}"

    bucket = bucket_name()
    if not bucket:
        return None

    try:
        from google.cloud import storage

        client = storage.Client(project=os.getenv("GCP_PROJECT_ID"))
        prefix = f"{project_id}/{name}." if project_id else ""
        for blob in client.list_blobs(bucket, prefix=prefix):
            stem = blob.name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            if stem == name:
                return f"{_GCS_SCHEME}{bucket}/{blob.name}"
    except Exception:  # noqa: BLE001 - 找不到就是找不到
        return None

    return None


def exists(uri: str) -> bool:
    """這個 URI 指的檔案在不在。**不下載內容。**

    為什麼要有這個函式：
        用 `load()` 確認檔案存在會把整支影片（幾十 MB）抓下來只為了回答一個是非題。

    GCS 那條走 `blob.exists()`，一次 metadata 請求，不傳內容。
    """
    if uri.startswith(_GCS_SCHEME):
        rest = uri[len(_GCS_SCHEME) :]
        bucket_id, _, blob_path = rest.partition("/")
        if not blob_path:
            return False
        try:
            from google.cloud import storage

            return (
                storage.Client(project=os.getenv("GCP_PROJECT_ID"))
                .bucket(bucket_id)
                .blob(blob_path)
                .exists()
            )
        except Exception:  # noqa: BLE001
            return False

    path = path_for(uri)
    return path is not None and path.exists()


def signed_url(uri: str, minutes: int = 60) -> str | None:
    """替 GCS 物件產生限時的直連下載網址。不是 GCS 或簽不出來回 None。

    為什麼要有這個函式：
        由 Cloud Run 讀進記憶體再吐給瀏覽器是拿應用伺服器當檔案伺服器，
        有三個代價：

        1. 32 MiB 天花板：Cloud Run 對非串流回應的硬限制，成片可超過
           （GCS 上檔案好好的、列表列得出來，就是下載不了，
           log 只寫「Response size was too large」）。
        2. 記憶體：幾十 MB 的檔案整包進 Python heap，容器只有 512 MiB。
        3. 頻寬與 CPU：每一次播放都經過服務，慢、而且要錢。

        簽名網址讓瀏覽器直接跟 GCS 拿，服務只負責授權。

    需要執行身分能對自己 signBlob（`roles/iam.serviceAccountTokenCreator`
    綁在自己身上）。Cloud Run 的預設 compute SA 已授權。

    本機開發一定回 None：使用者 ADC 沒有 `service_account_email`，
    簽不出來。那是預期行為，呼叫端會退回自己傳檔（本機沒有 32 MiB 限制）。

    Args:
        uri: 媒體 URI。只有 `gs://` 開頭的會處理。
        minutes: 網址有效期。預設一小時，足夠看完或下載完一支影片。

    Returns:
        限時網址；不是 GCS、或簽不出來（本機、缺權限）時回 None。
    """
    if not uri.startswith(_GCS_SCHEME):
        return None

    rest = uri[len(_GCS_SCHEME) :]
    bucket_id, _, blob_path = rest.partition("/")
    if not blob_path:
        return None

    try:
        from datetime import timedelta

        import google.auth
        import google.auth.transport.requests
        from google.cloud import storage

        credentials, _project = google.auth.default()
        # 拿 access token —— v4 簽名走 IAM SignBlob 時需要它。
        credentials.refresh(google.auth.transport.requests.Request())

        email = getattr(credentials, "service_account_email", None)
        if not email or email == "default":
            # 本機的使用者憑證沒有服務帳號身分，簽不出來。
            return None

        blob = (
            storage.Client(project=os.getenv("GCP_PROJECT_ID"))
            .bucket(bucket_id)
            .blob(blob_path)
        )
        return blob.generate_signed_url(
            version="v4",
            expiration=timedelta(minutes=minutes),
            method="GET",
            service_account_email=email,
            access_token=credentials.token,
        )
    except Exception:  # noqa: BLE001 - 簽不出來就讓呼叫端自己傳檔
        return None


def _kind_of(filename: str) -> str:
    """副檔名 → 前端要用哪種標籤渲染（image / video / other）。

    為什麼要有這個函式？
        媒體庫的每張卡片要決定用 `<img>` 還是 `<video>` 呈現。
        這個判斷放後端而不是前端，是因為「哪些副檔名算影片」跟
        `_EXT_BY_MIME`（存檔時的對照表）是同一組知識 ——
        分兩邊寫，加一種格式時一定會有一邊漏掉。

    認不得的一律回 "other"，前端顯示成檔名卡片而不是破圖。
    不要改成預設 "image"：破圖比誠實的「其他」難看也難查。

    Args:
        filename: 檔名（含副檔名）。不含點的檔名一律 other。

    Returns:
        "image" / "video" / "other" 三者之一。
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _VIDEO_EXTS:
        return "video"
    return "other"


def list_media(project_id: str) -> list[dict]:
    """列出一個專案所有生成的圖與影片，新到舊。

    為什麼要有這個函式？
        rewind 與 reset_batch 都刻意不刪檔案，生成的圖跟影片會一直累積。
        這個函式是媒體庫的資料來源，也是那些檔案的唯一入口。

    設計理由：
        使用者要能逐項挑選保留或刪除，而不是整批一起刪。
        所以清單裡每一項都要能被單獨挑出來刪：URI 是主鍵，
        呼叫 delete_one(uri) 直接處理。不做「一鍵清空」，
        那會鼓勵一次刪一堆，違反這個功能存在的理由。

    兩種後端一起列（設了 GCS 就查 GCS，本機資料夾也順便掃）：
    雲端遷移途中兩邊都可能有檔，少列一邊使用者會以為東西消失了。
    GCS 列不到時吞例外只回本機清單，不讓整頁 500。

    Args:
        project_id: 專案 ID。決定本機子目錄與 GCS 的 blob prefix。

    Returns:
        list[dict]，每項含 uri（delete_one / raw endpoint 的主鍵）、
        filename（給人看與下載）、kind（前端選 img/video）、
        size_bytes、created_at（ISO 字串）。依 created_at 新到舊排序。
    """
    items: list[dict] = []
    seen_uris: set[str] = set()

    folder = root() / project_id
    if folder.exists():
        for item in folder.iterdir():
            if not item.is_file() or item.name.startswith("."):
                continue
            uri = f"{_LOCAL_SCHEME}{project_id}/{item.name}"
            try:
                stat = item.stat()
                created = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                size = stat.st_size
            except OSError:
                continue
            items.append({
                "uri": uri,
                "filename": item.name,
                "kind": _kind_of(item.name),
                "size_bytes": size,
                "created_at": created.isoformat(),
            })
            seen_uris.add(uri)

    if bucket_name():
        try:
            from google.cloud import storage

            client = storage.Client(project=os.getenv("GCP_PROJECT_ID"))
            prefix = f"{project_id}/"
            for blob in client.list_blobs(bucket_name(), prefix=prefix):
                filename = blob.name[len(prefix):] if blob.name.startswith(prefix) else blob.name
                if not filename or filename.startswith("."):
                    continue
                uri = f"{_GCS_SCHEME}{bucket_name()}/{blob.name}"
                if uri in seen_uris:
                    continue
                created = blob.time_created or datetime.now(timezone.utc)
                items.append({
                    "uri": uri,
                    "filename": filename,
                    "kind": _kind_of(filename),
                    "size_bytes": blob.size or 0,
                    "created_at": created.isoformat(),
                })
        except Exception:  # noqa: BLE001 - 列不到就當本機的清單，不擋 UI
            pass

    items.sort(key=lambda x: x["created_at"], reverse=True)
    return items


def delete_one(uri: str) -> bool:
    """刪掉單一媒體檔。刪不到回 False 不拋例外。

    為什麼要有這個函式？
        delete_project 是整個專案的媒體一起刪；使用者需要的是逐項處置。
        整包刪跟逐項刪是兩種完全不同的意圖，不能用同一個函式。
        死檔案策略是一律保留，由媒體庫逐項刪。

    找不到檔案回 True 不是 bug，是刻意的冪等：
    使用者連按兩下刪除、或開兩個分頁刪同一項，第二次不該噴錯。
    「檔案現在不存在」就是呼叫端要的結果。

    不認得的 scheme 回 False（不是 True）：那代表 URI 格式有問題，
    不是「已經刪掉了」，這兩件事對呼叫端的意義不同。

    Args:
        uri: `local://{project}/{file}` 或 `gs://{bucket}/{path}`。
             通常來自 list_media() 回傳項目的 uri 欄位。

    Returns:
        True 代表檔案現在確定不存在（剛刪掉、或本來就不在）；
        False 代表刪除失敗或 URI 格式不認得。
    """
    # Demo 模式在這裡擋：demo 專案跟正式專案共用同一個 GCS bucket 與同一批檔案，
    # 訪客在 demo 站按一下「刪除」就會把正式成品刪掉。
    # 擋生成不等於擋破壞，所以刪除出口也要守。守在唯一出口，route 層不必各自記得。
    from . import demo_mode
    demo_mode.guard(f"刪除檔案（{uri.rsplit('/', 1)[-1]}）")

    if uri.startswith(_LOCAL_SCHEME):
        path = path_for(uri)
        if path is None:
            return False
        if not path.exists():
            return True  # 已經不在了就是刪掉了
        try:
            path.unlink()
            return True
        except OSError:
            return False

    if uri.startswith(_GCS_SCHEME):
        rest = uri[len(_GCS_SCHEME):]
        bucket, _, blob_path = rest.partition("/")
        if not blob_path:
            return False
        try:
            from google.cloud import storage

            blob = storage.Client(project=os.getenv("GCP_PROJECT_ID")).bucket(
                bucket
            ).blob(blob_path)
            if not blob.exists():
                return True
            blob.delete()
            return True
        except Exception:  # noqa: BLE001
            return False

    return False
