"""媒體庫 endpoint。

設計理由：
    生成的圖與影片品質參差，使用者要的是「好看的留、醜的刪」，
    不是全部一起刪。所以 media library 是逐個管的介面：列出所有圖與影片、
    每一項可以獨立預覽 / 下載 / 刪除。不是「一鍵清空」。

    rewind_to 與 reset_batch 都不刪檔案 —— 死檔案的收容所就是這裡。
    使用者事後回來逐項處置。

三個 endpoint 對應三個動作：
    列表        GET  /projects/{id}/media
    讀原始檔    GET  /projects/{id}/media/{filename}/raw
    單獨刪除    DELETE /projects/{id}/media/{filename}

filename 是 URI 的最後一段（不是完整 URI）—— URL path 帶 slash 不方便。
   後端優先查本機、找不到才查 GCS。同一 project 底下檔名帶隨機碼，不會撞。
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import RedirectResponse, StreamingResponse

from ..services import media_store

router = APIRouter()

# 一次送出去的塊大小。1 MiB 是串流影片的常見值 ——
# 太小會增加往返次數，太大就失去分塊的意義。
_CHUNK_BYTES = 1024 * 1024


def stream_bytes(data: bytes, chunk: int = _CHUNK_BYTES) -> Iterator[bytes]:
    """把一整包 bytes 切塊 yield 出去。

    為什麼要有這個函式：
        Cloud Run 對「非串流」回應有 32 MiB 上限。成片超過這個大小時
        GCS 上檔案好好的、列表也列得出來，但下載一律失敗，log 只寫一行
        `Response size was too large. Please consider reducing response size.`

        改成 chunked transfer 就繞過那個上限。這是只在真實雲端才會
        出現的失敗：本機 uvicorn 沒有這個限制，檔案再大都送得出去。

    這不省記憶體 —— 整包 bytes 還是先進了記憶體（`media_store.load()`
       的介面就是回 bytes）。它解決的是傳輸層的大小限制，不是記憶體。
       真的要省記憶體得讓 media_store 提供 streaming 讀取，那是另一件事。

    Args:
        data: 完整的檔案內容。
        chunk: 每塊大小。

    Yields:
        每次一塊 bytes。
    """
    for start in range(0, len(data), chunk):
        yield data[start : start + chunk]


def _safe_filename(filename: str) -> str:
    """擋 path traversal。純檔名、不能含斜線或 `..`。

    為什麼要有這個函式？
        filename 來自 URL path、是使用者可控的輸入，而它會被拼進
        `local://{project}/{filename}` 再轉成真實檔案路徑。
        沒有這道檢查，`..%2F..%2Fetc%2Fpasswd` 之類的輸入可以
        讀到或刪掉專案目錄以外的檔案。

    verify_wiring 有對應測項驗證它真的擋得住。

    不能刪：這是 media raw / delete 兩個 endpoint 唯一的輸入驗證。
       拿掉等於開一個任意檔案讀寫的洞。

    測試這條時不要用含 `%2F` 的 payload —— decode 後有斜線，
       FastAPI 會判成 route 不匹配直接回 404，根本走不到這裡。
       要測請用 `..hidden` 這種「route 匹配得到、但含 `..`」的檔名。

    Args:
        filename: URL path 傳進來的檔名。

    Returns:
        原樣回傳合法的 filename。

    Raises:
        HTTPException(400): 空字串、含 `/`、`\\` 或 `..`。
    """
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="檔名不合法")
    return filename


def _resolve_uri(project_id: str, filename: str) -> str | None:
    """把 (project_id, filename) 還原成完整的媒體 URI。優先本機、再查 GCS。

    為什麼要有這個函式？
        URL path 帶不了完整 URI（`local://` 和 `gs://` 都含斜線），
        所以 route 只收檔名。但 media_store 的 load / delete_one
        都以 URI 為主鍵 —— 中間需要這層還原。

        優先本機的理由：本機檔案存在時它就是實際會被讀到的那份
        （media_store.load 也是依 scheme 分派、本機優先），
        順序不一致的話會出現「列表顯示 A、刪掉的是 B」。

    配合 media_store 的雙後端並存策略。

    找不到回 None 不拋例外 —— 呼叫端自己決定要回 404（讀取）
       還是回「已經不存在」（刪除的冪等回應）。這兩種語意不同。

    Args:
        project_id: 專案 ID。
        filename: 已經過 _safe_filename 驗證的純檔名。

    Returns:
        `local://...` 或 `gs://...`；兩邊都找不到時回 None。
    """
    local_uri = f"local://{project_id}/{filename}"
    if media_store.path_for(local_uri) is not None:
        path = media_store.path_for(local_uri)
        if path is not None and path.exists():
            return local_uri

    bucket = media_store.bucket_name()
    if bucket:
        gcs_uri = f"gs://{bucket}/{project_id}/{filename}"
        try:
            from google.cloud import storage
            import os as _os

            blob = storage.Client(project=_os.getenv("GCP_PROJECT_ID")).bucket(
                bucket
            ).blob(f"{project_id}/{filename}")
            if blob.exists():
                return gcs_uri
        except Exception:  # noqa: BLE001
            pass

    return None


@router.get("/{project_id}/media")
def list_media(project_id: str) -> dict:
    """列出這個專案的所有生成媒體。新到舊。$0。

    為什麼要有這個 endpoint？
        媒體庫頁面的資料來源。沒有它，生成的圖與影片沒有任何 UI 入口 ——
        使用者看不到也刪不掉。

    多回一個 total_bytes：使用者要判斷「該不該清一波」時，
       看的是總佔用而不是檔案數。29 個檔案聽起來沒事，95 MB 就有感。

    Args:
        project_id: 專案 ID。不存在的專案回空清單而不是 404 ——
            媒體目錄本來就可能還沒建立。

    Returns:
        {project_id, count, total_bytes, items[]}，items 每項含
        uri / filename / kind / size_bytes / created_at。
    """
    items = media_store.list_media(project_id)
    total_bytes = sum(item.get("size_bytes", 0) for item in items)
    return {
        "project_id": project_id,
        "count": len(items),
        "total_bytes": total_bytes,
        "items": items,
    }


@router.get("/{project_id}/media/{filename}/raw")
def get_media_raw(project_id: str, filename: str) -> Response:
    """讀回原始檔案位元組。給前端 `<img>` / `<video>` 的 src 用。$0。

    為什麼要有這個 endpoint？
        媒體檔存在 GCS（正式）或使用者家目錄（開發），
        兩種都不是瀏覽器直接連得到的位置。GCS bucket 刻意不公開 ——
        專案的生成內容不該被任何人猜到網址就看得到。
        所以要有一個經過後端的讀取口。配合 MediaLibrary.tsx 的縮圖預覽。

    帶 Cache-Control: max-age=3600 —— 媒體檔內容不會變（檔名帶隨機碼），
       不快取的話捲動清單會重複下載幾十 MB 的影片。

    檔案讀不回來時回 404 而不是 500：檔案可能被外部手動刪掉，
       或是專案狀態從別台機器複製過來、帶著本機沒有的 URI。
       那種情況 UI 該顯示「檔案遺失」，不是整頁爆掉。

    雲端一律轉去 Cloud Storage 直供：
       檔案已經在 GCS，讓 Cloud Run 讀進記憶體再吐一次，等於拿應用
       伺服器當檔案伺服器 —— 撞 32 MiB 上限、吃掉容器的記憶體、
       每次播放都佔應用的頻寬。改成 302 到限時簽名網址，瀏覽器直接跟
       GCS 拿。

    本機開發簽不出網址（使用者 ADC 沒有服務帳號身分），
       退回自己分塊傳檔 —— 本機沒有 32 MiB 限制，這條路走得通。

    Args:
        project_id: 專案 ID。
        filename: 純檔名，會經過 _safe_filename 驗證。

    Returns:
        302 轉址到 GCS 簽名網址；本機則是分塊串流的檔案內容。
    """
    filename = _safe_filename(filename)
    uri = _resolve_uri(project_id, filename)
    if uri is None:
        raise HTTPException(status_code=404, detail="找不到這個媒體檔")

    # 生產路徑：讓瀏覽器直接去 GCS 拿，後端只負責授權。
    url = media_store.signed_url(uri)
    if url:
        return RedirectResponse(url, status_code=302)

    # 本機路徑：自己傳。
    loaded = media_store.load(uri)
    if loaded is None:
        raise HTTPException(status_code=404, detail="檔案讀不回來（可能被外部刪掉了）")

    data, mime = loaded
    return StreamingResponse(
        stream_bytes(data),
        media_type=mime,
        headers={
            "Cache-Control": "public, max-age=3600",
            "Content-Length": str(len(data)),
        },
    )


@router.delete("/{project_id}/media/{filename}")
def delete_media(project_id: str, filename: str) -> dict:
    """單獨刪一個媒體檔。$0，且不可復原。

    為什麼要有這個 endpoint？
        這是「死檔案策略」的執行端。rewind 與 reset_batch 都不刪檔案，
        全部沉澱到媒體庫；使用者事後回來逐項處置。

    刻意沒有批次刪除 / 全選。一次刪一個是設計，不是還沒做完 ——
       批次操作會鼓勵「全部清掉」，那正是這個功能要避免的行為。

    刪不到回 200 `{"deleted": false}` 而不是 404：
       使用者連按兩下、或多分頁刪同一項，都不該看到錯誤。
       「檔案已經不在了」對使用者來說就是成功。

    Args:
        project_id: 專案 ID。
        filename: 純檔名，會經過 _safe_filename 驗證。

    Returns:
        {deleted, uri?, filename?} 或 {deleted: False, reason}。
    """
    filename = _safe_filename(filename)
    uri = _resolve_uri(project_id, filename)
    if uri is None:
        return {"deleted": False, "reason": "檔案已經不存在"}

    ok = media_store.delete_one(uri)
    return {
        "deleted": ok,
        "uri": uri,
        "filename": filename,
    }
