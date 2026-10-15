-- Greenlight — ClickHouse schema
--
-- ═══════════════════════════════════════════════════════════════════════════
-- 這張表是整個產品的學習迴路
-- ═══════════════════════════════════════════════════════════════════════════
--
-- 系統的核心承諾是「告訴你這樣拍的成功率是多少」。
-- 冷啟動時那個數字來自 config/priors.yaml 的先驗值 —— 是估的。
--
-- 每跑一次真實生成就往這裡寫一筆，之後同類鏡頭的預估就改用實測統計。
-- 跑得越多越準。這就是 ClickHouse 在本專案的角色：
-- 不是拿來裝資料的地方，是讓「成功率預估」這個功能能夠變準的必要條件。
--
-- 為什麼是 ClickHouse 而不是 Postgres：
--   這裡的查詢全是「在幾十萬筆生成紀錄裡，按多個特徵欄位分組算通過率」，
--   典型的 OLAP 聚合。而且 applied_constraints 是陣列欄位，
--   要做 arrayJoin 之後再分組 —— ClickHouse 對這種查詢是原生強項。
--
-- ═══════════════════════════════════════════════════════════════════════════
-- 三個最重要的欄位
-- ═══════════════════════════════════════════════════════════════════════════
--
--   applied_constraints    套用了哪些約束規則
--   predicted_success_rate 當時預測的成功率
--   outcome                實際結果
--
-- 有這三個欄位，才回答得了兩個關鍵問題：
--   1. 「hand_anatomy 這條約束到底有沒有用？」
--      → 比較有套用與沒套用的實際通過率差異，用來校準 expected_lift
--   2. 「系統的預估準不準？」
--      → 比較 predicted_success_rate 與實際結果，用來校準 priors.yaml
--
-- 沒有這三個欄位，這張表就只是流水帳，學不到東西。

CREATE DATABASE IF NOT EXISTS greenlight;

-- ---------------------------------------------------------------------------
-- 生成紀錄
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS greenlight.generation_ledger
(
    -- ---- 識別 ----
    event_id            UUID,
    created_at          DateTime64(3, 'UTC'),
    plan_id             String,
    shot_id             String,

    -- 第幾次嘗試。1 = 首次生成，2 以上 = 重試。
    -- 成功率統計只看 attempt = 1 的紀錄 —— 重試時人會微調提示詞，
    -- 把重試也算進去會高估「一次過關率」。
    attempt             UInt8,

    -- ---- 鏡頭特徵（成功率預測的輸入）----
    -- 這些欄位必須與 backend/app/schemas/shot.py 的定義對得上。
    -- LowCardinality 是給列舉值用的，能大幅壓縮儲存並加速分組。
    style_id            LowCardinality(String),
    tier                LowCardinality(String),
    shot_type           LowCardinality(String),
    camera_move         LowCardinality(String),
    character_count     UInt8,
    base_duration_seconds UInt8,
    extensions          UInt8,
    has_dialogue        Bool,
    has_reference_image Bool,
    has_first_frame     Bool,
    content_flags       Array(LowCardinality(String)),

    -- ---- 套用了哪些約束 ----
    -- 這一欄讓「邊界設得好成功率就越高」變成可驗證的主張，而不只是信念。
    applied_constraints Array(LowCardinality(String)),

    -- 生成當下系統預測的成功率。用來回頭檢查預測準不準。
    predicted_success_rate Float32,

    -- ---- 結果 ----
    -- accepted  = 人採用了這個片段
    -- rejected  = 生出來了但不能用
    -- error     = API 失敗（配額、審核擋下、逾時）
    --
    -- error 不算進成功率統計 —— 那是基礎設施問題，不是鏡頭難度問題。
    -- 混在一起會讓「這種鏡頭很難拍」的結論被 API 故障污染。
    outcome             LowCardinality(String),

    -- 為什麼不能用。這是未來新增約束規則的線索來源 ——
    -- 某個 rejection_reason 反覆出現，就代表缺一條對應的約束。
    rejection_reason    LowCardinality(String) DEFAULT '',

    -- ---- 成本與效能 ----
    cost_usd            Float32,
    latency_seconds     UInt16,
    model_id            String,

    -- ---- 給人看的（除錯用，不進統計）----
    prompt_excerpt      String DEFAULT '',
    output_uri          String DEFAULT ''
)
ENGINE = MergeTree
-- 排序鍵按查詢模式設計：ledger.py 的相似度查詢是逐層放寬的，
-- 從 (style, tier, shot_type, camera_move) 全匹配開始往外退。
-- 排序鍵前綴與那個順序一致，每一層都能吃到索引。
ORDER BY (style_id, tier, shot_type, camera_move, created_at)
-- 按月分區。資料量不大，但分區讓「只看最近三個月」這種查詢能跳過舊分區 ——
-- 模型會改版，半年前的成功率統計參考價值有限。
PARTITION BY toYYYYMM(created_at)
TTL toDateTime(created_at) + INTERVAL 18 MONTH;

-- ---------------------------------------------------------------------------
-- 物化檢視：約束規則的實測效益
--
-- 用途：回答「hand_anatomy 這條約束到底有沒有用」。
--
-- 做法是把 applied_constraints 陣列展開（arrayJoin），
-- 每條規則各自統計「有套用時的通過率」。
--
-- 這個數字不能直接當成因果效益 —— 約束是依鏡頭內容自動觸發的，
-- 會套用 hand_anatomy 的鏡頭本來就比較難（有手部特寫）。
-- 所以直接比較「有套 vs 沒套」會得到反向的結論。
-- 正確的校準方式是控制其他特徵之後再比較，見 ledger.constraint_effectiveness()
-- 的實作說明。這個檢視只提供原始素材。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS greenlight.constraint_stats
(
    rule_id             LowCardinality(String),
    style_id            LowCardinality(String),
    shot_type           LowCardinality(String),
    day                 Date,
    attempts            UInt32,
    accepted            UInt32
)
ENGINE = SummingMergeTree
ORDER BY (rule_id, style_id, shot_type, day);

CREATE MATERIALIZED VIEW IF NOT EXISTS greenlight.constraint_stats_mv
TO greenlight.constraint_stats
AS
SELECT
    arrayJoin(applied_constraints) AS rule_id,
    style_id,
    shot_type,
    toDate(created_at)             AS day,
    count()                        AS attempts,
    countIf(outcome = 'accepted')  AS accepted
FROM greenlight.generation_ledger
WHERE attempt = 1 AND outcome != 'error'
GROUP BY rule_id, style_id, shot_type, day;

-- ---------------------------------------------------------------------------
-- 物化檢視：預測校準
--
-- 用途：回答「系統的成功率預估準不準」。
--
-- 把預測值分成 10 個桶（0-10%, 10-20%, ...），
-- 看每個桶的實際通過率是多少。
--
-- 理想狀態：預測 30% 的那一桶，實際通過率就該接近 30%。
-- 如果預測 30% 的實際只有 15%，代表 priors.yaml 整體偏樂觀，要下修。
--
-- 這張表是誠實原則的量化工具。系統宣稱的成功率準不準，這裡看得到。
-- ---------------------------------------------------------------------------
-- is_backfill 這一欄是校準報告能不能信的關鍵。
--
-- scripts/probes/backfill_ledger.py 把已經跑完的生成補進來時，
-- predicted_success_rate 是事後重算的 —— 生成當下根本沒有這個欄位。
-- 而 priors.yaml 與 constraints.yaml 是看著那些失敗結果調過的。
-- 拿那些列做校準等於拿答案回去改考卷，報告會顯得比真實情況準。
--
-- 旗標必須放在物化檢視裡，不能只在查詢時過濾 ——
-- MV 只按 (bucket, style, day) 聚合，plan_id 到不了這一層，
-- 事後再想排除就已經來不及了。
CREATE TABLE IF NOT EXISTS greenlight.calibration_stats
(
    predicted_bucket    UInt8,
    style_id            LowCardinality(String),
    day                 Date,
    is_backfill         UInt8,
    attempts            UInt32,
    accepted            UInt32
)
ENGINE = SummingMergeTree
ORDER BY (predicted_bucket, style_id, day, is_backfill);

CREATE MATERIALIZED VIEW IF NOT EXISTS greenlight.calibration_stats_mv
TO greenlight.calibration_stats
AS
SELECT
    toUInt8(floor(predicted_success_rate * 10)) AS predicted_bucket,
    style_id,
    toDate(created_at)                          AS day,
    toUInt8(plan_id LIKE '%#backfill')          AS is_backfill,
    count()                                     AS attempts,
    countIf(outcome = 'accepted')               AS accepted
FROM greenlight.generation_ledger
WHERE attempt = 1 AND outcome != 'error'
GROUP BY predicted_bucket, style_id, day, is_backfill;
