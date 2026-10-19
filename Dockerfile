# Greenlight —— Cloud Run（backend + frontend 一起）
#
# 為什麼前端一起打包：
#    單一 URL、Cloud Run 一個服務全包，部署與網域設定最簡單。
#    改 CSS 要重 deploy —— 但這不是頻繁更新的 SaaS，可接受。
#    分兩段 build：Stage 1 node 產出 dist，Stage 2 python 收進 backend/static/。

# ─── Stage 1: 前端靜態檔 ─────────────────────────────────────────
FROM node:20-slim AS frontend-builder
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci --silent
COPY frontend/ ./
RUN npm run build

# ─── Stage 2: 後端 + serve 前端 ──────────────────────────────────
FROM python:3.13-slim

# ffmpeg 由 pip 的 imageio-ffmpeg 提供（binary 打包在 wheel 裡），
# 所以這裡不裝系統 ffmpeg —— 系統依賴會在部署那天才咬人。
# 見 requirements.txt 的說明。

# 但字型必須裝：
#
#    ffmpeg 的 subtitles filter 靠 libass 渲染，libass 靠 fontconfig 找字型。
#    python:3.13-slim 裡一個字型都沒有 —— 於是燒字幕會
#    「成功」：ffmpeg 回 exit 0、後端回 200、檔案也產出來了，
#    就是畫面上一個字都沒有。
#
#    fonts-noto-cjk 是為了中文與日文字幕（本產品是三語的）；
#    DejaVu 很小，補齊拉丁字母的字重。
#    `fontconfig` 不能省，即使字型檔已經裝好了：
#       裝完 fonts-noto-cjk 之後 ffmpeg 仍然畫不出字，log 說的是
#       `Fontconfig error: Cannot load default config file` ——
#       有字型 ≠ 找得到字型。設定檔在 fontconfig 套件裡，
#       而 `--no-install-recommends` 正好把它跳過了。
RUN apt-get update && apt-get install -y --no-install-recommends \
        fontconfig \
        fonts-noto-cjk \
        fonts-dejavu-core \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依賴先裝，讓 Docker 快取住這一層 ——
# 改 code 不用重裝一次 google-cloud-aiplatform（它很大）。
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY config/ ./config/

# 前端 dist 進到 backend/app/static/：main.py 用 `Path(__file__).parent / "static"`
#    定位（__file__ 是 backend/app/main.py），所以 static 必須在 app/ 底下同層。
#    開發時沒有這個目錄就跳過（保留本機 vite dev 一路）。
COPY --from=frontend-builder /backend/app/static ./backend/app/static/

# config/ 一定要進 image：整個系統是 config 驅動的
#    （價目表、成功率先驗、物理約束、風格、選角規則、三語提示詞房間）。
#    漏了它 config_loader 會在啟動時就拒絕跑 —— 那個檢查是刻意的。

WORKDIR /app/backend

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Cloud Run 從 PORT 環境變數指定要監聽哪個埠，不能寫死。
# 不加 --reload：那會產生孤兒子進程，而且正式環境沒有東西要 reload。
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
