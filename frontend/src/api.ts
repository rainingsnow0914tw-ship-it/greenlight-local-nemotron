import { uiText } from './sysText'
/**
 * API client。
 *
 * 所有給人看的文字後端都回 {zh, en, ja} 三語 —— 前端拿到整包後自己切，
 * 換語言不用重打 API（見 backend/app/services/i18n.py 的說明）。
 */

/** 送出請求；伺服器回錯誤就丟出它說的原因（detail），讓畫面顯示，不要默默吞掉。 */
export async function send(url: string, init?: RequestInit): Promise<void> {
  const r = await fetch(url, init)
  if (r.ok) return
  let msg = `${r.status} ${r.statusText}`
  try {
    const j = await r.json()
    if (j?.detail) msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
  } catch {
    /* 回應不是 JSON：用狀態碼 */
  }
  throw new Error(uiText(msg))
}

export type Lang = 'zh' | 'en' | 'ja'

/** 後端回的雙／三語欄位。缺的語言會自動退回中文。 */
export type Localized = Partial<Record<Lang, string>>

export function t(value: Localized | string | undefined, lang: Lang): string {
  if (!value) return ''
  if (typeof value === 'string') return value
  return value[lang] || value.zh || value.en || ''
}

/**
 * 帶逾時的 fetch。
 *
 * 為什麼一定要有：
 *    後端請求若被平台（Cloud Run）在數百秒後砍掉，瀏覽器這邊的
 *    `fetch` 可能一直懸著沒有 resolve 也沒有 reject —— 於是元件裡
 *    `finally { setBusy(null) }` 永遠沒執行到，整個面板的按鈕
 *    全部鎖死。使用者再按，程式在第一行就 `if (busy) return`，
 *    連請求都沒送出。
 *
 *    log 上完全看不到這種故障（沒有請求 = 沒有紀錄），
 *    只能從「一段時間內零筆 POST」反推出來。
 *
 * 生成類的請求要給足夠長的上限（見 post()：本機一鏡 8 分鐘以上，給 45 分鐘）。
 *    比後端短的話，會在後端還在做、或正要回報錯誤時把連線切掉，
 *    使用者反而看不到結果。
 */
async function fetchWithTimeout(
  path: string,
  init: RequestInit = {},
  timeoutMs = 40_000,
): Promise<Response> {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)
  try {
    return await fetch(path, { ...init, signal: ctrl.signal })
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') {
      throw new Error(
        `等了 ${Math.round(timeoutMs / 1000)} 秒還是沒有回應。` +
          `這一次的動作可能沒有完成 —— 重新整理頁面看一下最新狀態再決定要不要重試。`,
      )
    }
    throw e
  } finally {
    clearTimeout(timer)
  }
}

async function get<T>(path: string): Promise<T> {
  const res = await fetchWithTimeout(path)
  if (!res.ok) {
    const body = await res.text()
    let detail = body.slice(0, 200)
    try {
      const parsed = JSON.parse(body)
      if (parsed?.detail) detail = String(parsed.detail)
    } catch {
      // 不是 JSON（基礎設施層的錯誤），保留原文前 200 字
    }
    throw new Error(`${res.status} ${path}\n${uiText(detail)}`)
  }
  return res.json() as Promise<T>
}

/* ─────────────────────────── 型別 ─────────────────────────── */

export interface ProgressSummary {
  project_id: string
  title: string
  stage: string
  /** 左側進度燈：stage 與走最遠那一段的較後者（只顯示用） */
  rail_stage?: string
  stage_label: string
  stage_description: string
  step: number
  total_steps: number
  next_stage_label: string | null
  blockers: string[]
  spent_usd: number
  /** 定調的三個維度。重整之後要靠它們把選過的狀態顯示回來。 */
  style_id?: string
  /** 敘事形式：用什麼方式講。跟 style_id 是兩回事。 */
  format_id?: string
  aspect_ratio?: string
  updated_at: string
  /** 內建範例的標示（三語分開寫）：這一例示範什麼、走哪條路 */
  sample_info?: { title: Record<string, string>; note: Record<string, string> } | null
}

export interface Character {
  name: string
  visual_description: string | null
  is_principal: boolean
  line_count: number
  first_appearance_scene: number | null
}

export interface ProjectDetail {
  progress: ProgressSummary
  screenplay: {
    title: string
    language: string
    scene_count: number
    total_seconds: number
    warnings: string[]
    characters: Character[]
  } | null
  spend: {
    llm_usd: number
    images_usd: number
    video_usd: number
    total_usd: number
    ceiling_usd: number
  }
  decisions: {
    at: string
    stage: string
    choice: string
    rationale: string
    followed_recommendation: boolean
  }[]
}

export interface Scene {
  scene_number: number
  setting: string
  location: string | null
  time_of_day: string
  action: string
  characters_present: string[]
  estimated_seconds: number
  source_line_start: number
  source_line_end: number
}

export interface Segment {
  segment_id: string
  order: number
  title: string
  scene_numbers: number[]
  estimated_seconds: number
  estimated_usd: number
  synopsis: string
  is_self_contained: boolean
  status: string
  status_label: string
}

/** 已存在的成品（段落成片或全片）。null = 還沒接/還沒合成。 */
export interface OutputState {
  seconds: number
  built_at: string
  /** 接完之後有內容更新過 —— 成品是舊的，要重接。正常狀態，不是錯誤。 */
  stale: boolean
  /** 已燒錄字幕的下載網址，key 是語言碼 */
  burned: Record<string, string>
}

/** 學習迴路現況 —— 把 ClickHouse 裡累積的東西可見化，否則使用者感受不到它有作用 */
export interface ContentScreening {
  scanned: boolean
  scanned_at?: string
  summary: string
  risks: Array<{
    category_id: string
    category: Localized
    severity: string
    general_advice: Localized
    line_start: number
    line_end: number
    excerpt: string
    is_dialogue: boolean
    /** why / suggestion 是模型針對這一段寫的中文，沒有三語版本 —— 型別上就分開 */
    note: { why: string; suggestion: string }
    confidence: string
    mute_retry_applicable: boolean
  }>
  warnings?: string[]
  cost_usd?: number
  high_count?: number
  estimated_usd?: number
  disclaimer?: Localized
}

export interface LearningState {
  database: string
  connected: boolean
  records: number
  first_pass_rate: number | null
  /** 內建範例才有：最終版收下幾鏡／共幾鏡（不顯示成功率） */
  sample_final?: { accepted: number; total: number } | null
  first_tries: number
  rejected: number
  distinct_shots: number
  spent_usd: number
  refined_notes: { text: string; sample_size: number; refined_at: string } | null
  refine_threshold: number
}

export interface SegmentsResponse {
  has_chapters: boolean
  chapter_marks: number
  continuity_problems: string[]
  plan: {
    plan_id: string
    label: string
    source: string
    rationale: string
    total_seconds: number
    average_seconds: number
    estimated_total_usd: number
    tier: string
    segments: Segment[]
  } | null
  film: OutputState | null
}

export interface StylePreset {
  id: string
  label: Localized
  one_liner: Localized
  best_for: Localized
  caution: Localized
  difficulty_modifier: number
}

export interface AspectRatioOption {
  value: string
  label: Localized
  hint: Localized
  difficulty_modifier: number
}

/* ─────────────────────────── 呼叫 ─────────────────────────── */

export const api = {
  health: () => get<{ status: string; clickhouse: string }>('/api/health'),

  projects: () => get<{ projects: ProgressSummary[] }>('/api/projects'),

  project: (id: string) => get<ProjectDetail>(`/api/projects/${id}`),

  scenes: (id: string) => get<{ scenes: Scene[] }>(`/api/projects/${id}/scenes`),

  sceneSource: (id: string, n: number) =>
    get<{ scene_number: number; source_line_start: number; source_line_end: number; text: string }>(
      `/api/projects/${id}/scenes/${n}/source`,
    ),

  // tier 不帶 → 後端用 config 的預設檔位估價
  // （不要在這裡寫死某個檔位：檔位下架後會拿已停用的價目估）
  segments: (id: string, tier?: string) =>
    get<SegmentsResponse>(
      `/api/projects/${id}/segments${tier ? `?tier=${tier}` : ''}`,
    ),

  /**
   * 學習迴路現況。**$0，唯讀。**
   * `id` 省略 = 跨專案全域（首頁用）。
   * 兩種範圍共用同一個回傳形狀，前端才能共用同一個面板組件 ——
   *    形狀一分岔就會變成兩份 UI，而兩份 UI 必然不同步。
   */
  learning: (id?: string) =>
    get<LearningState>(
      id ? `/api/projects/${id}/learning` : '/api/config/learning-overview',
    ),

  /**
   * ClickHouse 裡的**原始**紀錄。**$0，唯讀。**
   * `id` 省略 = 跨專案全域（首頁用）。
   * 統計可以編，一筆一筆帶時間戳與模型 id 的原始列編不出來 ——
   *    這條路徑存在的理由就是舉證，讓學習迴路可被驗證。
   */
  learningRecords: (id?: string, limit = 40) =>
    get<{
      available: boolean
      rows: Array<Record<string, string | number | boolean>>
      columns: string[]
      table?: string
      database?: string
      endpoint?: string
    }>(
      id
        ? `/api/projects/${id}/learning/records?limit=${limit}`
        : `/api/config/learning-records?limit=${limit}`,
    ),

  /** 內容安全預審結果。**$0**（沒掃過會回 scanned:false 與預估費用）。 */
  screening: (id: string) =>
    get<ContentScreening>(`/api/projects/${id}/screening`),

  /** 跑一次內容預審。**會花錢**（依劇本長度，通常 $0.05–0.2）。 */
  runScreening: (id: string) =>
    post<ContentScreening>(`/api/projects/${id}/screening`),

  styles: () =>
    get<{ default: string; presets: StylePreset[] }>('/api/config/styles'),

  /** 敘事形式（劇情／紀錄片／訪談／導覽）。**$0。**
   *  跟 styles 是兩個維度：這個管「用什麼方式講」，那個管「看起來像什麼」。 */
  formats: () =>
    get<{ default: string; formats: Array<{ id: string; label: Localized; one_liner: Localized }> }>(
      '/api/config/formats',
    ),

  aspectRatios: () =>
    get<{ options: AspectRatioOption[] }>('/api/config/aspect-ratios'),

  /**
   * 這個部署是正式版還是展示版。**$0。**
   * 展示版不呼叫 AI、不產生新檔案，所以上傳入口要先講明白 ——
   * 讓使用者在「解析」那一步才撞牆是最糟的體感。
   */
  runtime: () =>
    get<{ demo_mode: boolean; can_generate: boolean; can_upload: boolean }>(
      '/api/config/runtime',
    ),

  /**
   * 複製一份新專案：同劇本、進度歸零、原專案不受影響。**$0。**
   *
   * 用來「同一個劇本試不同風格」—— 真人版與動畫版並存在專案列表裡。
   * 劇本解析結果直接沿用，不必再付一次 ~$0.15。
   */
  duplicate: (id: string, title?: string) =>
    post<{ project_id: string; title: string; stage: string; note: string }>(
      `/api/projects/${id}/duplicate`,
      title ? { title } : undefined,
    ),

  /** 刪除之前先看會清掉多少。**$0，不刪任何東西。** */
  deletePreview: (id: string) =>
    get<{
      title: string
      will_delete: {
        media_files: number
        media_bytes: number
        spent_usd: number
      }
      will_keep: { learning_records: string }
    }>(`/api/projects/${id}/delete/preview`),

  /**
   * 刪掉專案**與它所有的圖檔影片檔**。不可逆。
   *
   * ClickHouse 的生成紀錄不刪 —— 那是系統學到的東西，跨專案累積，
   *    而且一筆只有幾百位元組。佔空間的是影片。
   */
  remove: async (id: string) => {
    const res = await fetch(`/api/projects/${id}`, { method: 'DELETE' })
    const data = await res.json()
    if (!res.ok) throw new Error(uiText(data.detail || `HTTP ${res.status}`))
    return data as {
      deleted: string
      title: string
      deleted_files: number
      freed_bytes: number
      kept: string
    }
  },
}

/* ─────────────────────── 段落 / 定調 / 分鏡 ─────────────────────── */

export interface PlanEstimate {
  segment: {
    order: number
    title: string
    scene_count: number
    estimated_seconds: number
    status: string
    has_shots: boolean
    format_id?: string | null
    effective_format_id?: string
  }
  estimated_input_tokens: number
  estimated_output_tokens: number
  estimated_cost_usd: number
  estimated_cost_low_usd: number
  estimated_cost_high_usd: number
  model_label: string
}

export interface ConstraintRule {
  rule_id: string
  category_label: string
  /** 這條規則想避免什麼。給人看的，不是給模型的那句 inject_text。 */
  principle: string
}

export interface ShotConstraints {
  applied: (ConstraintRule & { effective_lift: number })[]
  /**
   * 匹配到了但因為疊加上限沒送出去的規則。
   *
   * 這不是冷知識 —— 規則按預期提升排序後取前 6 條，所以
   *    content_flags 越多的鏡頭被丟掉越多，而那正是最難的鏡頭。
   */
  skipped: (ConstraintRule & { lost_lift: number })[]
  matched_count: number
  total_lift: number
  advisories: string[]
}

export interface Shot {
  shot_id: string
  scene_number: number
  base_duration_seconds: number
  extensions: number
  total_seconds: number
  shot_type: string
  camera_move: string
  description: string
  visual_prompt: string | null
  /** 使用者上傳的首幀（媒體庫 uri），有就原圖釘在第 0 格 */
  user_first_frame?: string | null
  characters_in_frame: string[]
  dialogue: string | null
  content_flags: string[]
  continues_from: string | null
  constraints: ShotConstraints
}

export interface Variant {
  tier: string
  label: string
  video_base_usd: number
  retry_expected_usd: number
  total_usd: number
  success_rate: number
  confidence: string
  budget_verdict: string
  riskiest: {
    shot_id: string
    success_rate: number
    expected_attempts: number
    /** 三語 —— 後端只算出風險 key，翻譯在 API 邊界發生（services/risk_text.py） */
    risk_factors: Localized[]
  }[]
}

export interface SegmentShots {
  segment: { order: number; title: string; format_id?: string | null; effective_format_id?: string }
  shot_list: {
    style_id: string
    aspect_ratio: string
    clip_count: number
    total_seconds: number
    api_call_count: number
    planning_notes: string[]
    shots: Shot[]
  }
  variants: Variant[]
  recommended_tier: string | null
  /** 使用者實際選定的檔位；null = 還沒選（後端會 fallback lite，但 UI 要催他選） */
  chosen_tier: string | null
  recommendation_reason: Localized
  test_shot_required: boolean
  test_shot_candidate: string | null
}

/**
 * 從回應裡挖出一句人看得懂的錯誤。
 *
 * 為什麼不能直接 `await res.json()`：
 *    HTTP 405 / 411 / 502 這些是基礎設施回的，body 是 HTML 不是 JSON。
 *    直接解析會拋 `SyntaxError: Unexpected token '<'` —— 那個訊息完全
 *    遮蔽了真正的狀態碼，畫面上看起來就像「按了什麼都沒發生」
 *    （例：角色名含斜線導致 405，畫面卻沒有任何有用的提示）。
 *    錯誤訊息要能指出往哪裡看。
 */
async function errorMessage(res: Response): Promise<string> {
  const text = await res.text()
  try {
    const data = JSON.parse(text)
    if (data?.detail) return String(data.detail)
  } catch {
    // 不是 JSON —— 基礎設施層的錯誤，狀態碼本身才是線索
  }
  const hint =
    res.status === 405
      ? '（路徑對不上，可能是名稱含特殊字元）'
      : res.status === 502 || res.status === 503
        ? '（上游服務暫時有問題，稍後再試）'
        : ''
  return `HTTP ${res.status} ${res.statusText}${hint}`
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  // 生成類的請求會跑好幾分鐘，其餘都是秒回。上限的用途是「不要永遠卡住」，不是精算每種請求該等多久。
  // 第三世試跑：本機 H3 一鏡 8 分鐘＋查證＋診斷，舊的 340 秒（Cloud Run 的 300 秒上限時代）
  //    在後端還在生的時候把連線切掉 —— 影片其實生好了，畫面卻說「沒有回應」。本機後端沒有 300 秒上限。
  const res = await fetchWithTimeout(
    path,
    {
      method: 'POST',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    },
    2_700_000,
  )
  if (!res.ok) throw new Error(uiText(await errorMessage(res)))
  return (await res.json()) as T
}

export const segmentApi = {
  /**
   * 設定**這一段**的敘事形式。**$0。**
   * 傳 null = 跟著專案走。
   * 段落層而不是專案層：同一個劇本第一章可能用旁白鋪陳、
   *    第二章讓角色自己演。
   */
  setFormat: (id: string, order: number, formatId: string | null) =>
    post<{
      order: number
      format_id: string | null
      effective_format_id: string
      project_format_id: string
      /** 已經分過鏡的話要重新分鏡才吃得到新策略 */
      needs_replan: boolean
    }>(`/api/projects/${id}/segments/${order}/format`, { format_id: formatId }),

  setLook: (
    id: string,
    look: { style_id: string; aspect_ratio: string; format_id?: string },
  ) => post<{ style_id: string; format_id: string; aspect_ratio: string; stage: string }>(
    `/api/projects/${id}/look`,
    look,
  ),

  setTier: (id: string, tier: string) =>
    post<{ chosen_tier: string; stage: string }>(`/api/projects/${id}/tier`, {
      tier,
    }),

  planEstimate: (id: string, order: number) =>
    get<PlanEstimate>(`/api/projects/${id}/segments/${order}/plan-estimate`),

  /**
   * 替這一段分鏡。**會花錢**（約 $0.05）。
   * force=true 是重新分鏡：會作廢這一段的試拍與批次進度
   *    （舊鏡頭是照舊分鏡表生的，鏡號與內容都對不上新表）。
   *    影片檔不刪，沉到媒體庫。
   */
  planShots: (id: string, order: number, force = false) =>
    post<{ clip_count: number; total_seconds: number; usage: { cost_usd: number } }>(
      `/api/projects/${id}/segments/${order}/plan-shots${force ? '?force=true' : ''}`,
    ),

  shots: (id: string, order: number) =>
    get<SegmentShots>(`/api/projects/${id}/segments/${order}/shots`),
}

/* ─────────────────────────── 選角關卡 ─────────────────────────── */

export interface CastingCandidate {
  candidate_id: string
  round_number: number
  interpretation: string
  appearance_tags: string
  inference_source: string
  inference_note: string
  cost_usd: number
  /** 生成失敗時為 null —— UI 要顯示「這張沒生出來」而不是破圖 */
  image_url: string | null
  has_image: boolean
  /** 音色描述（後端一直有給，之前前端沒用到） */
  voice_tags?: string
  /** 文字版候選（沒有生圖模型）：沒有圖但可以選定 */
  image_skipped?: boolean
  /** 這張候選屬於哪一段的造型輪；null = 一般選角輪 */
  segment_order: number | null
}

export interface RerollDirection {
  direction_id: string
  label: Localized
  hint: Localized
  prompt_modifier: string
  /** 造型模式可用（保臉的方向）；改臉的方向在造型模式不顯示 */
  look_variant?: boolean
}

export interface CastingSession {
  character_name: string
  kind: 'human' | 'non_human'
  is_principal: boolean
  source_description: string | null
  /** false 代表以下外貌是系統推測的 —— UI 必須標明（誠實原則） */
  has_explicit_description: boolean
  is_locked: boolean
  selected_candidate_id: string | null
  rounds_used: number
  total_spent_usd: number
  budget_notice: { level: string; message: string; spent_usd: number; rounds_used: number }
  directions: RerollDirection[]
  candidates: CastingCandidate[]
  // ── 身份參考（臉）與段落造型 ──
  identity_reference_url: string | null
  identity_source: 'uploaded' | 'generated' | null
  identity_consented_at: string | null
  /** {段落 order(字串): candidate_id} —— 該段用哪張造型；沒有 = 全片預設 */
  segment_looks: Record<string, string>
}

export interface CastingGate {
  can_proceed: boolean
  pending_characters: string[]
  blocked_reason: string
  total_spent_usd: number
  characters_locked: number
  characters_total: number
}

export interface CastingState {
  project_id: string
  gate: CastingGate
  round_cost_usd: number
  candidates_per_round: number
  sessions: CastingSession[]
  /** 專案的影片比例。選角卡的框要跟著它（圖已按專案比例生成） */
  aspect_ratio?: string
  /** 段落清單，給「造型用於：第 N 段」用；沒分段就是空陣列 */
  segments?: { order: number; title: string }[]
}

/**
 * 角色名一律走 query parameter，不要放進 URL path。
 *
 * 實測：角色名含斜線時（例：「Luna (月音/翎)」），`encodeURIComponent` 之後是
 * `Luna%20(%E6%9C%88%E9%9F%B3%2F%E7%BF%8E)` —— 看起來安全，但 Cloud Run 前端
 * 會把 path 裡的 `%2F` 還原成真正的斜線，路徑因此多出一段、route 對不上，
 * 回 405，而畫面上什麼都沒發生。名字乾淨的角色完全正常，
 * 所以症狀長得像「某幾個角色壞掉」。
 *
 * 角色名是劇本來的使用者資料，可能含任何字元。query string 沒有這個問題。
 */
export const castingApi = {
  state: (id: string) => get<CastingState>(`/api/projects/${id}/casting`),

  /** 會花錢（一輪約 $0.065），呼叫端要先讓使用者確認。
   *  segmentOrder 有值 = 為第 N 段生造型變體（角色要先鎖定，臉沿用身份參考） */
  generateRound: (id: string, character: string, direction = 'initial', segmentOrder?: number) =>
    post<{ session: CastingSession; gate: CastingGate; usage: Record<string, unknown> }>(
      `/api/projects/${id}/casting/rounds` +
        `?character=${encodeURIComponent(character)}` +
        `&direction=${encodeURIComponent(direction)}` +
        (segmentOrder != null ? `&segment_order=${segmentOrder}` : ''),
    ),

  /** segmentOrder 有值 = 只把這張設成第 N 段的造型，不動鎖定 */
  select: (id: string, character: string, candidateId: string, segmentOrder?: number) =>
    post<{ session: CastingSession; gate: CastingGate; stage: string }>(
      `/api/projects/${id}/casting/select` +
        `?character=${encodeURIComponent(character)}` +
        `&candidate_id=${encodeURIComponent(candidateId)}` +
        (segmentOrder != null ? `&segment_order=${segmentOrder}` : ''),
    ),

  /** segmentOrder 有值 = 只取消第 N 段的造型覆寫 */
  unlock: async (id: string, character: string, segmentOrder?: number) => {
    const res = await fetch(
      `/api/projects/${id}/casting/select` +
        `?character=${encodeURIComponent(character)}` +
        (segmentOrder != null ? `&segment_order=${segmentOrder}` : ''),
      { method: 'DELETE' },
    )
    const data = await res.json()
    if (!res.ok) throw new Error(uiText(data.detail || `HTTP ${res.status}`))
    return data as { session: CastingSession; gate: CastingGate; stage: string }
  },

  /** 上傳身份參考圖（臉）。$0。consent 必須為 true —— 後端也會擋。 */
  uploadIdentity: async (id: string, character: string, file: File, consent: boolean) => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(
      `/api/projects/${id}/casting/identity` +
        `?character=${encodeURIComponent(character)}` +
        `&consent=${consent ? 'true' : 'false'}`,
      { method: 'POST', body: form },
    )
    const data = await res.json()
    if (!res.ok) throw new Error(uiText(data.detail || data.message || `HTTP ${res.status}`))
    return data as { session: CastingSession; gate: CastingGate }
  },

  /** 上傳的那張原圖直接當這個角色（不經模型重畫；使用者自備人物卡） */
  useIdentity: async (id: string, character: string) => {
    const res = await fetch(
      `/api/projects/${id}/casting/identity/use?character=${encodeURIComponent(character)}`,
      { method: 'POST' },
    )
    const data = await res.json()
    if (!res.ok) throw new Error(uiText(data.detail || `HTTP ${res.status}`))
    return data as { session: CastingSession; gate: CastingGate }
  },

  clearIdentity: async (id: string, character: string) => {
    const res = await fetch(
      `/api/projects/${id}/casting/identity?character=${encodeURIComponent(character)}`,
      { method: 'DELETE' },
    )
    const data = await res.json()
    if (!res.ok) throw new Error(uiText(data.detail || `HTTP ${res.status}`))
    return data as { session: CastingSession; gate: CastingGate }
  },
}

/* ─────────────────────────── 提示詞導出 ─────────────────────────── */

export interface ShotPrompt {
  shot_id: string
  scene_number: number
  duration_seconds: number
  base_duration_seconds: number
  extensions: number
  aspect_ratio: string
  style: string
  reference_image: string | null
  first_frame_from: string | null
  /**
   * 這句話以什麼身份說出來。
   * narrator 走 Gemini TTS 另外配音、影片不帶人聲；其餘由影片模型生。
   */
  voice_role?: 'character' | 'offscreen' | 'narrator' 
  /** 分段內容：character_tags / style_prefix / framing / description / constraints / dialogue … */
  segments: Record<string, string>
  applied_constraints: { rule_id: string; category: string; principle: string }[]
  advisories: string[]
  /** 完整提示詞字串 —— 複製按鈕用的就是這個 */
  full: string
}

export interface PromptsResponse {
  project_id: string
  order: number
  title: string
  style_id: string
  aspect_ratio: string
  prompt_language: string
  /** false 代表提示詞裡沒有角色外貌標籤，生成出來的角色會長得不一樣 */
  casting_ready: boolean
  casting_warning: string
  /** 這一段實際出現、但還沒選定的角色。空陣列代表這一段的提示詞是完整的。 */
  characters_missing: string[]
  shot_count: number
  prompts: ShotPrompt[]
}

export const promptsApi = {
  /** 全部提示詞。**$0** —— 這一步不呼叫任何模型，提示詞是 code 組裝的。 */
  list: (id: string, order: number) =>
    get<PromptsResponse>(`/api/projects/${id}/segments/${order}/prompts`),

  /** 把畫面描述改寫成指定語言。**約 $0.007**（Flash，整段一次翻）。
   *  只改畫面描述，台詞不動 —— 台詞屬於影片語言。 */
  rewrite: (id: string, order: number, lang: Lang) =>
    post<{ rewritten: number; total: number; language: string; usage: { cost_usd: number } }>(
      `/api/projects/${id}/segments/${order}/prompts/rewrite?lang=${lang}`,
    ),

  /** 下載連結。直接給 <a href> 用，不經過 fetch —— 讓瀏覽器自己處理存檔。 */
  downloadUrl: (id: string, order: number, format: 'text' | 'markdown' | 'json') =>
    `/api/projects/${id}/segments/${order}/prompts/download?format=${format}`,
}

/* ─────────────────────── 試拍 / 批次 / 接片 ─────────────────────── */

export interface ClipRecord {
  shot_id: string
  status: 'not_started' | 'awaiting_approval' | 'approved' | 'failed'
  seconds: number
  width: number
  height: number
  /** **累計**花費，含重拍。重拍三次就是三倍。 */
  cost_usd: number
  attempts: number
  warnings: string[]
  error: string
  /**
   * 被內容安全審查擋下時的歸因。
   * API 只回一句籠統的 violates Responsible AI practices、不會說是哪一段，
   * 這是後端讀出來的推測 —— 是推測不是判決，UI 的措辭要保留餘地。
   */
  rejection?: {
    likely_cause: 'dialogue' | 'visual_description' | 'both' | 'unclear'
    excerpt: string
    category_id: string
    explanation: string
    suggestion: string
  }
  video_url: string | null
  /** 歷次生成成品，每個 take 獨立檔案、永不刪。uri 等頂層欄位反映「目前採用」的 take。 */
  takes: TakeRecord[]
  /** 本機查證與診斷（Greenlight Local verify/）。 */
  verify?: import('./components/VerifyBlock').Verify
  diagnosis?: import('./components/VerifyBlock').Diagnosis
  uri: string
}

export interface TakeRecord {
  attempt: number
  uri: string
  seconds: number
  width: number
  height: number
  size_bytes: number
  tier: string
  generated_at: string
}

export interface BatchState {
  total: number
  approved: number
  awaiting: number
  failed: number
  /** 選角與試拍兩道閘門都過了才是 true */
  can_start: boolean
  blocked_reason: string
  /** 選角關卡過了沒。false 時前端要在按下去之前就給去選角的入口 */
  casting_ready: boolean
  /** 還沒選定的角色名單 */
  pending_characters: string[]
  spent_usd: number
  remaining_estimate_usd: number
  tier_label: string
  next_shot: {
    shot_id: string
    description: string
    duration_seconds: number
    extensions: number
    cost_usd: number
  } | null
  clips: ClipRecord[]
  /** 這一段已接的成片（含 url）。F5 之後靠這個把下載／燒錄區還原回來。 */
  output: (OutputState & { url: string }) | null
}

export interface TestShotState {
  candidate_shot_id: string
  candidate_description: string
  duration_seconds: number
  tier_label: string
  estimated_cost_usd: number
  already_tested: boolean
  approved: boolean
  /** 選角關卡過了沒 —— 試拍也是生成，也要先選完角 */
  casting_ready: boolean
  casting_blocked_reason: string
  pending_characters: string[]
  record: {
    shot_id: string
    seconds: number
    width: number
    height: number
    cost_usd: number
    warnings: string[]
    video_url: string
    verify?: import('./components/VerifyBlock').Verify
    diagnosis?: import('./components/VerifyBlock').Diagnosis
  } | null
}

export const videoApi = {
  testShot: (id: string, order: number) =>
    get<TestShotState>(`/api/projects/${id}/segments/${order}/test-shot`),

  /** 會花錢（8 秒約 $0.40 起，依檔位），生成要 1–3 分鐘 */
  runTestShot: (id: string, order: number) =>
    post<{ record: { video_url: string }; spent_usd: number }>(
      `/api/projects/${id}/segments/${order}/test-shot`,
    ),

  approveTestShot: (id: string, order: number) =>
    post<{ approved: boolean; batch_estimate_usd: number | null }>(
      `/api/projects/${id}/segments/${order}/test-shot/approve`,
    ),

  batch: (id: string, order: number) =>
    get<BatchState>(`/api/projects/${id}/segments/${order}/batch`),

  /**
   * 會花錢。生一個鏡頭。
   *
   * `auto=false`（預設）：有鏡頭在等確認時後端會擋住 —— 逐鏡確認的重點。
   * `auto=true`：放行那道閘門，給一鍵模式連續呼叫用。
   *   **一鍵不等於自動通過** —— 生出來一樣停在待確認，最後一次過目。
   */
  nextClip: (id: string, order: number, auto = false, shotId?: string) =>
    post<BatchState>(
      `/api/projects/${id}/segments/${order}/batch/next?${new URLSearchParams({
        ...(auto ? { auto: 'true' } : {}), ...(shotId ? { shot_id: shotId } : {}) })}`,
    ),

  approveClip: (id: string, order: number, shotId: string) =>
    post<BatchState>(
      `/api/projects/${id}/segments/${order}/batch/${shotId}/approve`,
    ),

  /** 會花錢，而且累計不歸零。mute=true 是無聲重拍（台詞被內容審查擋下時，
   *  畫面照生、台詞改由字幕呈現） */
  /** $0 —— 把這一鏡「採用的版本」切到指定 take，檔案都在、隨時切回。 */
  chooseTake: (id: string, order: number, shotId: string, attempt: number) =>
    post<BatchState>(
      `/api/projects/${id}/segments/${order}/batch/${shotId}/take/${attempt}`,
    ),
  retryClip: (id: string, order: number, shotId: string, mute = false) =>
    post<BatchState>(
      `/api/projects/${id}/segments/${order}/batch/${shotId}/retry${mute ? '?mute=true' : ''}`,
    ),

  resetBatch: async (id: string, order: number) => {
    const res = await fetch(`/api/projects/${id}/segments/${order}/batch`, {
      method: 'DELETE',
    })
    const data = await res.json()
    if (!res.ok) throw new Error(uiText(data.detail || `HTTP ${res.status}`))
    return data as { cleared_clips: number; unlocked_voices: string[]; batch: BatchState }
  },

  /** 接片。**$0** —— 純本機 ffmpeg，不呼叫任何模型 */
  assemble: (id: string, order: number) =>
    post<{
      output_url: string
      seconds: number
      clip_count: number
      missing_shots: string[]
      warning: string
    }>(`/api/projects/${id}/segments/${order}/assemble`),

  /** 字幕下載連結。原文語言 $0，其他語言第一次會翻譯（約 $0.002），之後走快取 */
  subtitleUrl: (id: string, order: number, lang: Lang) =>
    `/api/projects/${id}/segments/${order}/subtitles?lang=${lang}`,

  /**
   * 把字幕**燒進影片**，產出自帶字幕的 mp4。**$0**（本機 ffmpeg，會重新編碼、比較慢）。
   *
   * 跟 subtitleUrl 的差別：那個給你一個 .srt 要自己合成，這個直接給你成品。
   * 另存新檔，不覆蓋原始成片 —— 想換語言或要無字幕版，原檔都還在。
   */
  burnSubtitles: (id: string, order: number, lang: Lang) =>
    post<{
      download_url: string
      filename: string
      lang: string
      size_bytes: number
      uri: string
    }>(`/api/projects/${id}/segments/${order}/assemble/burn?lang=${lang}`),

  downloadUrl: (id: string, order: number) =>
    `/api/projects/${id}/segments/${order}/download`,
}

/* ─────────────────────────── 全片 ─────────────────────────── */

export interface FilmAssembleResult {
  download_url: string
  seconds: number
  segment_count: number
  /** 還沒接片、因此不在全片裡的段落序號。誠實回報，不靜默跳過。 */
  missing_segments: number[]
  warning: string
}

export const filmApi = {
  /** 把所有已接片的段落合成一部完整的片。**$0**（ffmpeg -c copy，不重編碼）。 */
  assemble: (id: string) =>
    post<FilmAssembleResult>(`/api/projects/${id}/assemble-film`),

  downloadUrl: (id: string) => `/api/projects/${id}/film/download`,

  /** 全片字幕。時間軸已帶各段偏移 —— 不是把各段 srt 直接接起來。 */
  subtitleUrl: (id: string, lang: Lang) =>
    `/api/projects/${id}/film/subtitles?lang=${lang}`,

  /** 把字幕燒進整部片。整片重編碼，會跑幾分鐘。 */
  burn: (id: string, lang: Lang) =>
    post<{
      download_url: string
      filename: string
      lang: string
      size_bytes: number
      uri: string
    }>(`/api/projects/${id}/film/burn?lang=${lang}`),
}

/* ─────────────────────────── 媒體庫 ─────────────────────────── */

export type MediaKind = 'image' | 'video' | 'other'

export interface MediaItem {
  uri: string
  filename: string
  kind: MediaKind
  size_bytes: number
  created_at: string
}

export interface MediaListResponse {
  project_id: string
  count: number
  total_bytes: number
  items: MediaItem[]
}

export const mediaApi = {
  list: (id: string) => get<MediaListResponse>(`/api/projects/${id}/media`),

  /** 給 <img> / <video> 的 src 直接用 —— 走 GET raw endpoint */
  rawUrl: (id: string, filename: string) =>
    `/api/projects/${id}/media/${encodeURIComponent(filename)}/raw`,

  /** 單獨刪一個。冪等：連按兩下不會噴 500。 */
  deleteOne: async (id: string, filename: string) => {
    const res = await fetch(
      `/api/projects/${id}/media/${encodeURIComponent(filename)}`,
      { method: 'DELETE' },
    )
    const data = await res.json()
    if (!res.ok) throw new Error(uiText(data.detail || `HTTP ${res.status}`))
    return data as {
      deleted: boolean
      uri?: string
      filename?: string
      reason?: string
    }
  },
}

/* ─────────────────────────── Rewind ─────────────────────────── */

export interface RewindPreview {
  project_id: string
  current: string
  current_label: string
  target: string
  target_label: string
  can_rewind: boolean
  invalidated: string[]
}

export interface RewindCommitResult {
  project_id: string
  target: string
  target_label: string
  invalidated: string[]
  cleared: Record<string, number | boolean>
  new_stage: string
  note?: string
}

export const stateApi = {
  /** 先看退回代價，不改任何狀態 */
  rewindPreview: (id: string, target: string) =>
    get<RewindPreview>(
      `/api/projects/${id}/rewind/preview?target=${encodeURIComponent(target)}`,
    ),

  /** 真的退回。檔案一律留給媒體庫，不會刪。 */
  rewindCommit: (id: string, target: string) =>
    post<RewindCommitResult>(`/api/projects/${id}/rewind`, { target }),
}
