"""Greenlight API。

設計理由：
    這一層刻意做得很薄 —— 所有邏輯都在 services/ 與 agents/ 裡，
    routes 只負責把它們接到 HTTP 上。

    薄的好處：前端要什麼欄位就加什麼 endpoint，不會因為改介面
    就動到成本計算或成功率預估的邏輯。

雙語：所有給人看的文字都回 {"zh": ..., "en": ...}。
   前端拿到兩種語言後自己切換，不用為了換語言重打 API
   （見 services/i18n.py 的說明）。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .services import env_boot

# 必須在其他 import 之前執行：
#    agents 在 module 層就可能讀環境變數，載入順序晚了就讀到空值。
#    症狀是「同一份 code 在某個 shell 起得來、換個 shell 就說沒設定 GCP_PROJECT_ID」。
env_boot.load()

from .routes import casting as casting_routes  # noqa: E402
from .routes import config as config_routes  # noqa: E402
from .routes import ui as ui_routes  # noqa: E402
from .routes import media as media_routes  # noqa: E402
from .routes import projects as project_routes  # noqa: E402
from .routes import prompts as prompt_routes  # noqa: E402
from .routes import screening as screening_routes  # noqa: E402
from .routes import settings as settings_routes  # noqa: E402
from .routes import direction as direction_routes  # noqa: E402
from .routes import language as language_routes  # noqa: E402
from .routes import verify as verify_routes  # noqa: E402
from .routes import finish as finish_routes  # noqa: E402
from .routes import video as video_routes  # noqa: E402
from .services import config_loader, demo_mode  # noqa: E402

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """啟動時檢查設定檔與 schema 有沒有對上。

    對不上就讓服務起不來 —— 帶著錯誤設定跑起來，比起不來更危險。
       （例如 priors.yaml 少了某個鏡別的修正項，成功率會靜默算錯。）
    """
    problems = config_loader.verify_config_integrity()
    if problems:
        for p in problems:
            logger.error("設定檔問題：%s", p)
        raise RuntimeError(
            "設定檔與 schema 不同步，服務拒絕啟動：\n  - " + "\n  - ".join(problems)
        )
    logger.info("設定檔一致性檢查通過")
    try:   # 內建範例：第一次開 app 放進專案清單（失敗不能擋啟動）
        from .services import sample

        logger.info("sample: %s", sample.install_if_needed())
    except Exception:  # noqa: BLE001
        logger.exception("sample install failed")
    try:   # app 自己裝的 ComfyUI：重開機後沒人啟動它 → 開 app 就順手開（背景、不擋啟動）
        import threading

        from .machine import installer

        threading.Thread(target=installer.ensure_running, daemon=True, name="comfy-autostart").start()
    except Exception:  # noqa: BLE001
        logger.exception("comfy autostart failed")
    yield


SAMPLE_READ_ONLY = (
    "This is the built-in sample — a finished run to look through, read only. "
    "To try it yourself, start a new project with your own script."
)


async def _sample_read_only(request, call_next):
    """內建範例是「做好的一次完整流程」，給人看懂系統在做什麼，不讓人重跑或改掉（只准刪除整個範例）。
    擋在唯一的入口：所有寫入範例專案的請求一律 403，畫面會照實顯示原因。"""
    from fastapi.responses import JSONResponse

    path, method = request.url.path, request.method
    if method in ("POST", "PUT", "PATCH", "DELETE") and path.startswith("/api/projects/p-sample-"):
        rest = path[len("/api/projects/"):].strip("/")
        if not (method == "DELETE" and "/" not in rest):     # 刪除整個範例專案可以
            return JSONResponse({"detail": SAMPLE_READ_ONLY}, status_code=403)
    return await call_next(request)


app = FastAPI(
    title="Greenlight Local · NVIDIA Nemotron Edition API",
    description="劇本轉影片的製片規劃系統",
    version="0.1.0",
    lifespan=lifespan,
)

app.middleware("http")(_sample_read_only)

# 開發期允許 Vite dev server。
# 正式部署時前端會跟 API 同源，這段就用不到了。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(project_routes.router, prefix="/api/projects", tags=["projects"])
# 選角關卡掛在同一個 prefix 底下 —— 它是專案狀態的一部分，
# 分成獨立檔案只是因為「一功能一檔」，不是因為它是另一個資源。
app.include_router(casting_routes.router, prefix="/api/projects", tags=["casting"])
app.include_router(prompt_routes.router, prefix="/api/projects", tags=["prompts"])
app.include_router(video_routes.router, prefix="/api/projects", tags=["video"])
# 內容安全預審：只給資訊不擋流程
app.include_router(
    screening_routes.router, prefix="/api/projects", tags=["screening"]
)
# 媒體庫掛在同 prefix —— 是專案的子資源（生成的圖與影片）。
# 讓使用者事後回來挑「好看的留、醜的刪」。
app.include_router(media_routes.router, prefix="/api/projects", tags=["media"])
app.include_router(config_routes.router, prefix="/api/config", tags=["config"])
# Greenlight Local：設定頁（使用者自己的金鑰、每一步用哪個 AI）與機器檢查
app.include_router(settings_routes.router, prefix="/api/settings", tags=["settings"])
app.include_router(settings_routes.machine_router, prefix="/api/machine", tags=["machine"])
# Greenlight Local：導演工作（場面調度 → 導演迴圈 → 分鏡表），背景跑、畫面讀進度
app.include_router(direction_routes.router, prefix="/api/projects", tags=["direction"])
# 學語言模式：台詞導演（口語、讀音、雙語字幕、學習筆記）＋學習字幕版成片
app.include_router(language_routes.router, prefix="/api/projects", tags=["language"])
# 本機查證與診斷：生完一鏡 → 程式查證 → 對照症狀庫診斷 → 修法（verify/）
app.include_router(verify_routes.router, prefix="/api/projects", tags=["verify"])
app.include_router(verify_routes.symptoms_router, prefix="/api", tags=["verify"])
# 成片流程：剪頭 → 放大 → 轉場 → 接片 → 燒字幕（services/finish.py）
app.include_router(finish_routes.router, prefix="/api/projects", tags=["finish"])
app.include_router(ui_routes.router, prefix="/api/ui", tags=["ui"])


@app.get("/api/health")
def health() -> dict:
    """健康檢查。也順便回報 ClickHouse 通不通 ——
    連不上不是錯誤（系統會走降級模式用先驗值），但要看得到。

    也回報速率調節器學到的狀態。批次生成時使用者會等好幾分鐘，
       「系統正在依配額放慢」跟「系統卡死了」看起來一模一樣 ——
       把學到的間隔攤開來，等待才是可理解的。
    """
    from .services import assembler, ledger, rate_governor

    # 字型也要看得到：沒有字型時燒字幕會「成功」——
    #    ffmpeg exit 0、HTTP 200、檔案產出，就是畫面上一個字都沒有。
    #    這種靜默失敗只能靠「開機就檢查」抓，不能等使用者交片前才發現。
    return {
        "status": "ok",
        "clickhouse": "connected" if ledger.is_available() else "degraded",
        "fonts": assembler.font_report(),
        "rate_governors": rate_governor.snapshot_all(),
    }


@app.exception_handler(FileNotFoundError)
def handle_not_found(_request, exc: FileNotFoundError):
    raise HTTPException(status_code=404, detail=str(exc))


@app.middleware("http")
async def demo_read_only(request: Request, call_next):
    """Demo 模式＝唯讀：任何非 GET/HEAD/OPTIONS 的請求一律 402。

    為什麼要在最外層再擋一次，而不是只靠三道守衛：
        三道守衛（genai_client／video_gen／media_store.save）守的是「不花錢」。
        但 demo 站還有一整類「不花錢卻會改狀態」的操作 —— 解除選定、退回階段、改風格、
        改檔位、段落造型、刪媒體檔 —— 訪客按一下，之後的訪客看到的就是被弄壞的 demo；
        刪媒體檔更糟：demo 跟正式版共用同一個 bucket，會刪掉正式成品。
        逐個 route 補守衛一定會漏（新 route 容易忘），所以在門口一刀：demo 只讀。
        已生成的成果照樣播放與下載（都是 GET），學習迴路照樣真查（GET）。
    """
    if demo_mode.is_demo() and request.method not in ("GET", "HEAD", "OPTIONS"):
        exc = demo_mode.DemoBlocked("修改或刪除（展示版唯讀）", "", 0.0)
        return JSONResponse(status_code=402, content=exc.as_detail())
    return await call_next(request)


@app.exception_handler(demo_mode.DemoBlocked)
async def handle_demo_blocked(_request: Request, exc: demo_mode.DemoBlocked):
    """Demo 模式擋下花錢操作時，回一句人話而不是一個堆疊。

    用 402 Payment Required 而不是 403/500 —— 語意剛好對：
       這個操作是合法的、你也有權限，只是在這個部署裡不付錢。
       前端靠 `demo_mode: true` 這個欄位就能認出來、顯示成說明卡而不是錯誤。

    明說「正式版會呼叫什麼、花多少」，不假裝成功也不給死按鈕。
       跟產品其他地方同一個原則：降級可以，靜默不行。
    """
    return JSONResponse(status_code=402, content=exc.as_detail())


@app.middleware("http")
async def no_stale_index(request: Request, call_next):
    """頁面入口（index.html）每次都重新拿，不讓瀏覽器用舊的：
    範例 3：app 更新後畫面還是舊版（新按鈕不見），因為瀏覽器快取了舊的 index.html，它指向舊的 js。
    /assets/* 檔名帶版本雜湊（每次 build 都不同），照常快取；/api/* 不管。"""
    response = await call_next(request)
    path = request.url.path
    if not path.startswith("/api/") and not path.startswith("/assets/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(404)
async def spa_fallback(request: Request, exc):
    """前端路徑找不到就回 index.html，讓瀏覽器端的路由自己處理。

    為什麼要有這個：
        `StaticFiles(html=True)` 只會在「目錄底下剛好有 index.html」時
        fallback。使用者在段落頁按 F5、或把網址貼給別人，瀏覽器會直接
        向伺服器要 `/segment/1` —— 那個檔案不存在，於是回 404 JSON，
        畫面變成一行 `{"detail":"Not Found"}`。

        從首頁點進去都正常，只有重新整理與分享連結會壞。

    `/api/*` 必須維持 JSON 404。前端靠狀態碼與 detail 訊息判斷，
       回 HTML 會讓每一個「找不到專案」變成看不懂的解析錯誤。

    靜態資源（`/assets/*`）也維持 404。回 index.html 給一個 .js 請求，
       瀏覽器會拿 HTML 當 JavaScript 解析，錯誤訊息完全不知所云。
    """
    path = request.url.path
    if (
        path.startswith("/api/")
        or path.startswith("/assets/")
        or not _STATIC_INDEX.is_file()
    ):
        return JSONResponse(
            {"detail": getattr(exc, "detail", "Not Found")}, status_code=404
        )
    return FileResponse(_STATIC_INDEX)


# 前端靜態檔 mount 必須在所有 API route 之後，否則 "/" 會攔截 /api/*。
#    Dockerfile Stage 1 build 出來的 dist 進到 backend/app/static/；
#    本機 vite dev 時沒這個目錄 —— 條件 mount，開發不受影響。
_STATIC_DIR = Path(__file__).parent / "static"
_STATIC_INDEX = _STATIC_DIR / "index.html"
if _STATIC_DIR.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=str(_STATIC_DIR), html=True),
        name="frontend",
    )
    logger.info("前端靜態檔已 mount：%s", _STATIC_DIR)
else:
    logger.info("前端靜態檔目錄不存在（%s），跳過 mount。本機 vite dev 模式。", _STATIC_DIR)
