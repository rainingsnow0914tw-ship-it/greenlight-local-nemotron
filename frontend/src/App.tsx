/**
 * Greenlight —— 主畫面。
 *
 * 流程：
 *   丟一個檔進來 → 看檔案資訊與費用 → 確認才解析 → 看結果
 *
 * 不做 mock。畫面上每個數字都來自真實的 API 回應。
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
} from 'react'
import { api, type Lang, type ProgressSummary } from './api'
import { COPY, LANGS, STAGE_DESCRIPTIONS, STAGE_NAMES, STAGE_ORDER } from './i18n'
import { Icon, IconDefs, Logo, STAGE_ICONS } from './components/Icons'
import { LearningPanel } from './components/LearningPanel'
import { SettingsPanel } from './components/SettingsPanel'
import { Dropzone } from './components/Dropzone'
import { UploadReview, type UploadResult } from './components/UploadReview'
import { ProjectView } from './components/ProjectView'
import { SegmentView } from './components/SegmentView'
import { CastingView } from './components/CastingView'
import { MediaLibrary } from './components/MediaLibrary'

type View =
  | { kind: 'home' }
  | { kind: 'review'; upload: UploadResult }
  | { kind: 'project'; id: string }
  | { kind: 'segment'; id: string; order: number }
  // 選角是專案層不是段落層：角色必須全片共用，
  // 否則同一個人在不同章節長得不一樣。
  | { kind: 'casting'; id: string }
  // 媒體庫也是專案層：所有生成的圖與影片都在這裡逐項處置。
  | { kind: 'media'; id: string }
  // Greenlight Local：設定頁（機器檢查、使用者自己的金鑰、每一步用哪個 AI、影片引擎）
  | { kind: 'settings' }

/**
 * 落櫻的參數表。
 *
 * 放在 code 裡而不是 CSS 的 nth-of-type：
 *    nth-of-type 算「同標籤的第幾個」，而 blob 和 dust 也是 <span>，
 *    用選擇器指定會編號錯位（最後幾片變隱形）。
 *    靠 DOM 順序做樣式本來就脆弱，中間插一個元素就全毀。
 *
 * 數值刻意不等距。均勻分布看起來就是機器排的，
 * 真實的落櫻是稀疏而不規則的。
 * 負的 delay 讓它們一載入就在半空中，不會全部從頂端一起開始掉。
 */
const PETALS = [
  { left: '6%', dur: 21, delay: -2, drift: 70, size: 20, pale: false },
  { left: '15%', dur: 27, delay: -9, drift: -50, size: 15, pale: true },
  { left: '24%', dur: 18, delay: -14, drift: 110, size: 20, pale: false },
  { left: '33%', dur: 24, delay: -5, drift: -80, size: 23, pale: false },
  { left: '44%', dur: 29, delay: -18, drift: 60, size: 18, pale: true },
  { left: '53%', dur: 20, delay: -11, drift: -95, size: 17, pale: false },
  { left: '62%', dur: 26, delay: -3, drift: 85, size: 20, pale: true },
  { left: '71%', dur: 16, delay: -20, drift: -60, size: 14, pale: false },
  { left: '80%', dur: 23, delay: -7, drift: 100, size: 21, pale: false },
  { left: '88%', dur: 28, delay: -16, drift: -70, size: 18, pale: true },
  { left: '96%', dur: 19, delay: -24, drift: -120, size: 18, pale: false },
]

const DUST = [
  { top: '22%', left: '18%', delay: 0, fuji: false },
  { top: '64%', left: '74%', delay: -4.5, fuji: false },
  { top: '38%', left: '58%', delay: -8, fuji: true },
  { top: '78%', left: '32%', delay: -11, fuji: true },
]

const SETTINGS_LABEL: Record<Lang, string> = { zh: '設定', en: 'Settings', ja: '設定' }

export default function App() {
  const [lang, setLang] = useState<Lang>(() => {
    const saved = localStorage.getItem('greenlight.lang')
    return (saved === 'en' || saved === 'ja' ? saved : 'zh') as Lang
  })
  const [view, setView] = useState<View>({ kind: 'home' })
  const [projects, setProjects] = useState<ProgressSummary[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  /** 展示版旗標。demo 站不呼叫 AI，上傳入口要先講明白而不是讓人撞牆。 */
  const [demoMode, setDemoMode] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  const c = COPY[lang]

  useEffect(() => {
    // 讀不到就當正式版 —— 寧可讓按鈕可按（後端還有守衛），
    // 也不要因為一個唯讀查詢失敗就把整個入口鎖住。
    api.runtime().then((r) => setDemoMode(r.demo_mode)).catch(() => {})
  }, [])

  useEffect(() => {
    localStorage.setItem('greenlight.lang', lang)
    document.documentElement.lang =
      lang === 'zh' ? 'zh-TW' : lang === 'ja' ? 'ja' : 'en'
  }, [lang])

  const refresh = useCallback(async () => {
    try {
      const data = await api.projects()
      setProjects(data.projects)
      setError(null)
    } catch (e) {
      setError(String(e))
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  // 穩定參照 —— 見下方 CastingView 的說明。
  const handleGateChange = useCallback(() => {
    void refresh()
  }, [refresh])

  /*
   * 傳給子元件的 callback，只要可能被子元件列進 useCallback 的相依，
   *    就必須是穩定參照（useCallback 或 setState 函式），不可以寫 inline。
   *
   *    若 `onGateChange={() => void refresh()}` 是 inline，
   *    CastingView 的 load 依賴它 → load 重建 → useEffect 重跑 → 打 API
   *    → onGateChange → refresh → 重繪 → 新函式 → 無限迴圈
   *    （實測六秒 12 次 API，而使用者按的生成一次都沒送出去）。
   *
   *    現況盤點（六個元件的 load 都依賴 onError）：
   *        LookPanel / MediaLibrary / ProductionPanel /
   *        ProjectView / PromptsPanel / SegmentView
   *    它們現在安全，只因為 `onError={setError}` 是 setState 函式。
   *    若改成 `onError={(m) => setError('前綴' + m)}`，
   *       那個元件立刻無限迴圈 —— 而症狀是「頁面卡卡的」，極難查。
   *
   *    寫新元件時：load 的相依只放資料鍵（projectId / order），
   *    callback 一律用 ref 接（CastingView 有現成範例）。
   */

  const handleFile = useCallback(async (file: File) => {
    setBusy(true)
    setError(null)
    try {
      const form = new FormData()
      form.append('file', file)
      const res = await fetch('/api/projects/upload', {
        method: 'POST',
        body: form,
      })
      const body = await res.json()
      if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`)
      setView({ kind: 'review', upload: body as UploadResult })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }, [])

  // 目前所在階段 —— 左側光柱用
  const currentStage =
    view.kind === 'project' || view.kind === 'segment'
      ? projects.find((p) => p.project_id === view.id)?.stage
      : view.kind === 'review'
        ? 'script_uploaded'
        : undefined

  return (
    <>
      {/*
        動く背景 —— hero-blob 手法。
        三片印泥的暈在紙上緩慢漂浮（19–27 秒一個週期），
        四粒塵在斜光裡起落。
        aria-hidden：這是氛圍不是內容，讀屏軟體不該念它。
      */}
      <div className="washi-bg" aria-hidden="true">
        <span className="blob blob-1" />
        <span className="blob blob-2" />
        <span className="blob blob-3" />

        {/*
          鳥居 —— 中央的大剪影，水彩感：要有層次與顏色，但不能太實色，
          放在中間、尺寸大一點。

          水彩的三個要素，都用 SVG 原生做：
            1. 濃淡    linearGradient —— 上濃下淡，像顏料往下流
            2. 不規則  feTurbulence + feDisplacementMap —— 邊緣被水推開
            3. 暈開    重影一層放大模糊的自己，像顏料滲出輪廓

          不用圖片：向量在任何解析度都乾淨，顏色也能跟主題走。
        */}
        <svg className="torii" viewBox="0 0 420 400">
          <defs>
            {/*
              朱（しゅ）—— 稻荷鳥居的丹塗り。
              原則一：鳥居要跳出來，其他都是低語。
              所以主色是飽和的朱紅，不是柔化過的粉。

              原則二：邊緣淡入霧中。
              所以下段轉成藤紫並大幅降低不透明度 ——
              柱腳消失在霧裡，不是硬生生切斷。
            */}
            <linearGradient id="shuInk" x1="0.12" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#D93B2B" stopOpacity="0.92" />
              <stop offset="22%" stopColor="#D2402F" stopOpacity="0.86" />
              <stop offset="55%" stopColor="#BC4A45" stopOpacity="0.62" />
              <stop offset="80%" stopColor="#96566F" stopOpacity="0.34" />
              <stop offset="100%" stopColor="#7B6096" stopOpacity="0.1" />
            </linearGradient>

            {/* 滲出輪廓的暈 —— 顏料先跑出去的那一層 */}
            <linearGradient id="shuBleed" x1="0.2" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#E2543F" stopOpacity="0.4" />
              <stop offset="60%" stopColor="#B85C6B" stopOpacity="0.22" />
              <stop offset="100%" stopColor="#8C7FAE" stopOpacity="0.05" />
            </linearGradient>

            {/*
              陰影 —— 水彩的陰影用互補色不用灰。
              朱紅的補色在藍紫，所以受光的反面壓一層藍紫，
              顏色會「活」，用灰的話整張會髒掉。
            */}
            <linearGradient id="shuShade" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#5B4A80" stopOpacity="0.38" />
              <stop offset="70%" stopColor="#6B5B8C" stopOpacity="0.2" />
              <stop offset="100%" stopColor="#7B6096" stopOpacity="0.04" />
            </linearGradient>

            {/* 水推開顏料的邊緣 */}
            <filter id="washEdge" x="-14%" y="-10%" width="128%" height="122%">
              <feTurbulence
                type="fractalNoise"
                baseFrequency="0.024"
                numOctaves="4"
                seed="11"
                result="grain"
              />
              <feDisplacementMap
                in="SourceGraphic"
                in2="grain"
                scale="11"
                xChannelSelector="R"
                yChannelSelector="G"
              />
              <feGaussianBlur stdDeviation="0.8" />
            </filter>

            {/* 滲出去的那層推得更開、糊得更多 */}
            <filter id="washBleed" x="-22%" y="-16%" width="144%" height="132%">
              <feTurbulence
                type="fractalNoise"
                baseFrequency="0.013"
                numOctaves="3"
                seed="4"
                result="grain"
              />
              <feDisplacementMap in="SourceGraphic" in2="grain" scale="30" />
              <feGaussianBlur stdDeviation="7" />
            </filter>

            {/*
              邊緣沉積（edge darkening / blooming）——
              水彩乾燥時顏料會被推到邊界聚積，形成一圈較深的輪廓。
              這是水彩最好認的特徵，少了它就只是「模糊的色塊」。

              做法：把圖形往內侵蝕一圈，用原圖扣掉侵蝕後的版本，
              剩下的就是邊界那一環。
            */}
            <filter id="edgePool" x="-14%" y="-10%" width="128%" height="122%">
              <feTurbulence
                type="fractalNoise"
                baseFrequency="0.024"
                numOctaves="4"
                seed="11"
                result="grain"
              />
              <feDisplacementMap
                in="SourceGraphic"
                in2="grain"
                scale="11"
                xChannelSelector="R"
                yChannelSelector="G"
                result="wobbled"
              />
              <feMorphology in="wobbled" operator="erode" radius="2.5" result="inner" />
              <feComposite in="wobbled" in2="inner" operator="out" result="ring" />
              <feGaussianBlur in="ring" stdDeviation="1.1" />
            </filter>

            {/*
              紙紋 —— feTurbulence 產生噪紋，用 diffuse lighting 打出凹凸，
              再 multiply 疊回顏料上。顏料坐在紙的纖維上，不是平塗的色塊。
            */}
            <filter id="paperGrain" x="0%" y="0%" width="100%" height="100%">
              <feTurbulence
                type="fractalNoise"
                baseFrequency="0.9"
                numOctaves="4"
                seed="3"
                result="noise"
              />
              <feDiffuseLighting
                in="noise"
                lightingColor="#fff"
                surfaceScale="1.4"
                result="lit"
              >
                <feDistantLight azimuth="45" elevation="62" />
              </feDiffuseLighting>
              <feComposite in="lit" in2="SourceGraphic" operator="in" />
            </filter>

            {/* 鳥居本體 —— 定義一次，各層共用 */}
            <g id="toriiShape">
              {/* 笠木：最上面的橫樑，兩端上翹 */}
              <path d="M10 44 Q210 20 410 44 L410 68 Q210 46 10 68 Z" />
              {/* 島木 */}
              <rect x="36" y="72" width="348" height="19" />
              {/* 額束：中央的小柱 */}
              <rect x="197" y="95" width="26" height="42" />
              {/* 貫：第二根橫樑，比笠木短 */}
              <rect x="52" y="137" width="316" height="17" />
              {/* 左柱：微微外八，真實鳥居的收分 */}
              <path d="M64 91 L46 400 L84 400 L93 91 Z" />
              {/* 右柱 */}
              <path d="M356 91 L374 400 L336 400 L327 91 Z" />
            </g>

            {/* 受光的反面 —— 右柱右半、橫樑下緣。光從左上來 */}
            <g id="toriiShade">
              <path d="M356 91 L374 400 L352 400 L344 91 Z" />
              <rect x="36" y="84" width="348" height="7" />
              <rect x="52" y="147" width="316" height="7" />
              <path d="M10 60 Q210 38 410 60 L410 68 Q210 46 10 68 Z" />
            </g>
          </defs>

          {/* 水彩的疊法：先滲出去的暈，再主色，再陰影，最後邊緣沉積 */}
          <use href="#toriiShape" fill="url(#shuBleed)" filter="url(#washBleed)" />
          <use href="#toriiShape" fill="url(#shuInk)" filter="url(#washEdge)" />
          <use href="#toriiShade" fill="url(#shuShade)" filter="url(#washEdge)" />
          <use href="#toriiShape" fill="#8E2A1E" opacity="0.5" filter="url(#edgePool)" />
          {/* 紙的纖維，壓在所有顏料上 */}
          <use href="#toriiShape" filter="url(#paperGrain)" opacity="0.14" />
        </svg>

        {/* 櫻 —— 十一片，各自的速度與漂移 */}
        {PETALS.map((p, i) => (
          <span
            className="petal"
            key={i}
            style={
              {
                left: p.left,
                width: p.size,
                height: p.size,
                background: p.pale ? 'var(--sakura-pale)' : 'var(--sakura)',
                animationDuration: `${p.dur}s`,
                animationDelay: `${p.delay}s`,
                '--drift': `${p.drift}px`,
              } as CSSProperties
            }
          />
        ))}

        {/* 塵 —— 斜光裡的浮塵 */}
        {DUST.map((d, i) => (
          <span
            className="dust"
            key={i}
            style={
              {
                top: d.top,
                left: d.left,
                background: d.fuji
                  ? 'rgba(183, 163, 204, 0.55)'
                  : 'rgba(228, 169, 190, 0.55)',
                animationDelay: `${d.delay}s`,
              } as CSSProperties
            }
          />
        ))}
      </div>

      <IconDefs />

      <div className="app">
        <nav className="rail">
        <div className="rail-mark">
          <Logo size={76} />
          <p className="rail-brand" style={{ margin: 0 }}>{c.brand}</p>
        </div>
        <p className="rail-tagline">{c.tagline}</p>
        <ol className="rail-stages">
          {STAGE_ORDER.map((stage) => {
            const idx = STAGE_ORDER.indexOf(stage)
            const curIdx = currentStage ? STAGE_ORDER.indexOf(currentStage) : -1
            const cls =
              curIdx < 0
                ? ''
                : idx < curIdx
                  ? 'done'
                  : idx === curIdx
                    ? 'current'
                    : ''
            return (
              <li key={stage} className={`rail-stage ${cls}`}>
                {/* 走到的那一關放大：目前那關 60px，其他 40px。

                    尺寸在這裡算，不在 CSS 覆蓋：SVG 元素上用 CSS width
                    蓋掉 width attribute 是規格的灰色地帶，實測 Chrome 沒吃
                    `.rail-stage.current .rail-stage-icon { width: 60px }`
                    —— 規則在樣式表裡、選擇器也匹配，computed 仍是 40px。
                    改在 JSX 算就沒有這個問題。

                    也不用 plain：那是給 20px 以下用的（濾鏡會推爛邊緣、
                    淡彩會糊成色斑），這個尺寸反過來需要淡彩與水彩邊。 */}
                <Icon
                  name={STAGE_ICONS[stage] ?? 'script'}
                  size={idx === curIdx ? 60 : 40}
                  className="rail-stage-icon"
                />
                {STAGE_NAMES[lang][stage]}
              </li>
            )
          })}
        </ol>
      </nav>

        <main className="main">
        <div className="topbar">
          <div>
            {view.kind !== 'home' && (
              <button
                className="back"
                onClick={() => {
                  setView(
                    view.kind === 'segment' ||
                      view.kind === 'casting' ||
                      view.kind === 'media'
                      ? { kind: 'project', id: view.id }
                      : { kind: 'home' },
                  )
                  void refresh()
                }}
              >
                {c.back}
              </button>
            )}
          </div>
          <div className="lang-switch">
            <button
              className={view.kind === 'settings' ? 'on' : ''}
              onClick={() => setView({ kind: 'settings' })}
              aria-pressed={view.kind === 'settings'}
            >
              {SETTINGS_LABEL[lang]}
            </button>
            {LANGS.map((l) => (
              <button
                key={l.code}
                className={l.code === lang ? 'on' : ''}
                onClick={() => setLang(l.code)}
                aria-pressed={l.code === lang}
              >
                {l.label}
              </button>
            ))}
          </div>
        </div>

        {error && (
          <div className="notice warn">
            <strong>{c.errorTitle}</strong>
            <div style={{ fontFamily: 'var(--mono)', fontSize: 12, marginTop: 6 }}>
              {error}
            </div>
          </div>
        )}

        {view.kind === 'settings' && <SettingsPanel lang={lang} />}

        {view.kind === 'home' && (
          <>
            {/* 學習迴路放在第一個畫面 —— 它是產品的核心主張，不該只活在專案內頁。
                用的就是專案內頁那一個組件（不傳 projectId = 全站累積）：
                同一份資料兩份 UI 會不同步。 */}
            <LearningPanel lang={lang} hideWhenUnavailable />
            <p className="eyebrow">{c.brand}</p>
            <h1>{c.dropTitle}</h1>

            <Dropzone
              copy={c}
              busy={busy}
              onFile={handleFile}
              onPick={() => fileInput.current?.click()}
              demo={demoMode}
              lang={lang}
            />
            <input
              ref={fileInput}
              type="file"
              accept=".txt,.md,.markdown,.fountain,.docx,.pdf"
              style={{ display: 'none' }}
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) void handleFile(f)
                e.target.value = ''
              }}
            />

            <section className="section">
              <div className="section-head">
                <h2>{c.projectsTitle}</h2>
                <span className="section-note">{projects.length}</span>
              </div>
              {projects.length === 0 ? (
                <p className="empty">{c.noProjects}</p>
              ) : (
                <div className="segments">
                  {projects.map((p) => (
                    <div
                      key={p.project_id}
                      className="segment"
                      role="button"
                      tabIndex={0}
                      onClick={() => setView({ kind: 'project', id: p.project_id })}
                      onKeyDown={(e) =>
                        e.key === 'Enter' &&
                        setView({ kind: 'project', id: p.project_id })
                      }
                    >
                      <div className="segment-no">{p.step}</div>
                      <div>
                        <p className="segment-title">
                          {/* 每個劇本名稱前面都帶劇本 icon，列表一致 */}
                          <Icon name="script" size={80} className="title-icon" />
                          {p.title}
                        </p>
                        <p className="segment-synopsis">
                          {STAGE_NAMES[lang][p.stage] ?? p.stage_label} ·{' '}
                          {STAGE_DESCRIPTIONS[lang][p.stage] ?? p.stage_description}
                        </p>
                      </div>
                      <div className="segment-meta">
                        <div className="segment-cost">
                          ${p.spent_usd.toFixed(2)}
                        </div>
                        <div>
                          {p.step}/{p.total_steps}
                        </div>
                        {/* 展示版兩顆都不給：
                            刪除是不可逆的，而 demo 站只有一個專案 ——
                            訪客按一下整個展示就沒了；複製則會讓列表長出
                            訪客建的空專案，敘事就散掉。後端也有守衛，
                            這裡只是不給死按鈕。 */}
                        {!demoMode && (
                        <>
                        {/* 同一個劇本想試別的風格就複製一份 —— 舊的留著，
                            不同風格用不同專案來分。
                            stopPropagation：卡片本身是「進入專案」的按鈕。 */}
                        <button
                          className="btn ghost small"
                          title={{ zh: "用同一份劇本開一個全新的版本，進度歸零，這個專案不受影響", en: "Start a fresh version from the same script; this project stays as it is", ja: "同じ台本から新しいバージョンを始めます（このプロジェクトはそのまま）" }[lang]}
                          onClick={async (e) => {
                            e.stopPropagation()
                            setBusy(true)
                            setError(null)
                            try {
                              const r = await api.duplicate(p.project_id)
                              await refresh()
                              setView({ kind: 'project', id: r.project_id })
                            } catch (err) {
                              setError(
                                err instanceof Error ? err.message : String(err),
                              )
                            } finally {
                              setBusy(false)
                            }
                          }}
                        >
                          {c.duplicateProject}
                        </button>
                        {/* 刪除是不可逆的，所以先問後端「會清掉什麼」再確認。
                            刪的是圖檔影片檔與專案文檔；ClickHouse 學到的
                            東西保留 —— 那是跨專案累積的記憶，不隨專案刪除。 */}
                        <button
                          className="btn ghost small"
                          title={{ zh: "刪掉這個專案與它所有的圖檔影片檔", en: "Delete this project and all of its images and videos", ja: "このプロジェクトと、その画像・動画をすべて削除します" }[lang]}
                          onClick={async (e) => {
                            e.stopPropagation()
                            setError(null)
                            try {
                              const pv = await api.deletePreview(p.project_id)
                              const mb = (
                                pv.will_delete.media_bytes /
                                1024 /
                                1024
                              ).toFixed(1)
                              const ok = window.confirm(
                                `${c.deleteConfirm}\n\n` +
                                  `「${pv.title}」\n` +
                                  `${c.deleteFiles}：${pv.will_delete.media_files}（${mb} MB）\n` +
                                  `${c.deleteSpent}：$${pv.will_delete.spent_usd.toFixed(2)}\n\n` +
                                  `${c.deleteKeeps}`,
                              )
                              if (!ok) return
                              setBusy(true)
                              await api.remove(p.project_id)
                              await refresh()
                            } catch (err) {
                              setError(
                                err instanceof Error ? err.message : String(err),
                              )
                            } finally {
                              setBusy(false)
                            }
                          }}
                        >
                          {c.deleteProject}
                        </button>
                        </>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>
          </>
        )}

        {view.kind === 'review' && (
          <UploadReview
            copy={c}
            lang={lang}
            upload={view.upload}
            onCancel={async () => {
              await fetch(`/api/projects/${view.upload.project_id}`, {
                method: 'DELETE',
              })
              setView({ kind: 'home' })
              void refresh()
            }}
            onParsed={(id) => {
              setView({ kind: 'project', id })
              void refresh()
            }}
            onError={setError}
          />
        )}

        {view.kind === 'project' && (
          <ProjectView
            copy={c}
            lang={lang}
            projectId={view.id}
            onError={setError}
            onOpenSegment={(order) =>
              setView({ kind: 'segment', id: view.id, order })
            }
            onOpenCasting={() => setView({ kind: 'casting', id: view.id })}
            onOpenMedia={() => setView({ kind: 'media', id: view.id })}
          />
        )}

        {view.kind === 'casting' && (
          <CastingView
            copy={c}
            lang={lang}
            projectId={view.id}
            onError={setError}
            // 一定要用穩定的參照，不要寫成 `() => void refresh()`：
            //    inline 函式每次 render 都是新的，
            //    子元件若把它列進 useCallback 的相依，就會
            //    load → setState → 重繪 → 新函式 → load … 無限打 API。
            onGateChange={handleGateChange}
          />
        )}

        {view.kind === 'media' && (
          <MediaLibrary
            lang={lang}
            projectId={view.id}
            onError={setError}
          />
        )}
        {/* SegmentView 必須在 <main> 之內。
            若寫在 </main> 之後，React 照樣 render，
            但整段跑到版面容器外 —— 畫面看起來「點進段落就空白」，
            而 console 沒有錯誤、API 全部 200，非常難查。
            症狀是版面問題不是資料問題時，先確認元素在不在該在的容器裡。 */}
        {view.kind === 'segment' && (
          <SegmentView
            copy={c}
            lang={lang}
            projectId={view.id}
            order={view.order}
            onError={setError}
            onOpenCasting={() => setView({ kind: 'casting', id: view.id })}
          />
        )}
        </main>
      </div>
    </>
  )
}
