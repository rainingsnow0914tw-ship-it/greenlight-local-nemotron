/**
 * 內容安全預審面板。
 *
 * 為什麼存在：
 *    「丟劇本之後就要審查機制，看有沒有裡面的內容違反平台或者是模型的規定，
 *      然後生成不了影片或圖片不很糟糕嗎？」
 *    在這之前，內容問題只有在生成當下才會發現 —— 實際使用時是拍到第 29 場、
 *    花了 $3 之後才撞上第一次。
 *
 * 三條界線（違反任何一條這個面板就從幫忙變成礙事）：
 *    1. **不擋流程** —— 掃出風險照樣可以往下做，警告留著就好
 *    2. **不改原稿** —— 只給意見，改不改是使用者的事
 *    3. **不承諾通過** —— 文案永遠是「掃出 N 處」，不是「已通過檢查」
 */

import { Icon } from './Icons'
import { useEffect, useState } from 'react'
import { api, t, type ContentScreening, type Lang, type Localized } from '../api'

const COPY: Record<string, Localized> = {
  title: { zh: '內容預審', en: 'Content pre-check', ja: '内容事前チェック' },
  why: {
    zh: '影片模型會對每一次生成做內容審查，被擋下時整鏡生不出來、而且它不會說是哪一段。這一關先幫你把可能過不了的地方標出來。',
    en: 'Video models run a safety filter on every generation. When it fires the whole shot is rejected — and it never says which part caused it. This pass flags the passages most likely to trip it.',
    ja: '動画モデルは生成のたびに内容審査を行い、引っかかるとカット全体が生成されません。しかもどの部分が原因かは教えてくれません。ここで事前に危なそうな箇所を洗い出します。',
  },
  run: { zh: '掃描劇本', en: 'Scan screenplay', ja: '脚本をスキャン' },
  rescan: { zh: '重新掃描', en: 'Scan again', ja: '再スキャン' },
  running: { zh: '掃描中…', en: 'Scanning…', ja: 'スキャン中…' },
  estimate: { zh: '預估 ${n}', en: 'about ${n}', ja: '約 ${n}' },
  clean: {
    zh: '這次掃描沒有標出高風險段落。',
    en: 'This scan found no high-risk passages.',
    ja: '今回のスキャンでは高リスク箇所は見つかりませんでした。',
  },
  found: {
    zh: '掃出 {n} 處需要注意（其中 {h} 處高風險）',
    en: '{n} passages worth a look ({h} high risk)',
    ja: '{n} 箇所に注意（うち高リスク {h} 箇所）',
  },
  line: { zh: '第 {a}–{b} 行', en: 'lines {a}–{b}', ja: '{a}〜{b} 行目' },
  dialogue: { zh: '台詞', en: 'Dialogue', ja: '台詞' },
  narration: { zh: '敘述', en: 'Narration', ja: 'ナレーション' },
  why2: { zh: '可能的問題', en: 'Why it may fail', ja: '想定される問題' },
  suggestion: { zh: '建議', en: 'Suggestion', ja: '提案' },
  muteHint: {
    zh: '這是台詞 —— 就算真的被擋，也可以用「無聲重拍」：畫面照生、這句話改用字幕呈現，劇本一個字都不用改。',
    en: 'This is dialogue — even if it is rejected you can use “silent reshoot”: the visuals still render and the line appears as a subtitle instead. The script stays untouched.',
    ja: 'これは台詞です。仮に拒否されても「無音で撮り直し」が使えます：映像はそのまま生成し、この台詞は字幕で表示します。脚本は一切変更不要です。',
  },
  confidence: { zh: '把握度', en: 'Confidence', ja: '確度' },
  failed: { zh: '掃描失敗：', en: 'Scan failed: ', ja: 'スキャン失敗：' },
  recommend: { zh: '建議先做', en: 'Do this first', ja: '最初にどうぞ' },
  costNote: {
    zh: '約 ${n} · 只讀不改，你的劇本原文一個字都不會動',
    en: 'About ${n} · read-only — your screenplay is never modified',
    ja: '約 ${n} · 読み取りのみ。脚本は一切変更されません',
  },
}

const CONFIDENCE: Record<string, Localized> = {
  high: { zh: '高', en: 'High', ja: '高' },
  medium: { zh: '中', en: 'Medium', ja: '中' },
  low: { zh: '低', en: 'Low', ja: '低' },
}

function fill(s: string, vars: Record<string, string | number>): string {
  return s.replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? ''))
}

export function ScreeningPanel({
  lang,
  projectId,
}: {
  lang: Lang
  projectId: string
}) {
  const [data, setData] = useState<ContentScreening | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    api
      .screening(projectId)
      .then((d) => {
        if (alive) setData(d)
      })
      .catch(() => {
        /* 讀不到就不顯示，不打擾主流程 */
      })
    return () => {
      alive = false
    }
  }, [projectId])

  async function run() {
    setBusy(true)
    setError('')
    try {
      setData(await api.runScreening(projectId))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (!data) return null

  const risks = data.risks || []
  const high = data.high_count ?? 0

  // ── 還沒掃過：整塊做成行動卡 ─────────────────────────────
  //
  // 按鈕太小、不顯眼，使用者找半天才找到。
  //    兩個原因疊在一起：
  //      1. class 寫成 `ghost` 而 CSS 定義的是 `.btn.ghost` ——
  //         **那顆按鈕連基本按鈕樣式都沒套上**，變成一行裸文字。
  //      2. 就算樣式對了，ghost 也是最淡的次要樣式，還塞在標題右邊。
  //    一個「使用者根本不知道存在」的功能，等於沒做。
  //
  // 掃完之後才收斂成次要按鈕 —— 那時候主角是結果，不是這顆按鈕。
  if (!data.scanned) {
    return (
      <section className="section screening-cta">
        <div className="section-head">
          <h2>
          <Icon name="screening" size={80} className="section-icon" />
          {t(COPY.title, lang)}
        </h2>
          <span className="tag">{t(COPY.recommend, lang)}</span>
        </div>
        <p className="prompts-note">{t(COPY.why, lang)}</p>
        {error && <div className="notice warn">{t(COPY.failed, lang) + error}</div>}
        <div className="screening-cta-action">
          <button className="btn primary" onClick={run} disabled={busy}>
            {busy ? t(COPY.running, lang) : t(COPY.run, lang)}
          </button>
          {/* 價錢與「不會動你的稿子」放按鈕旁邊而不是塞進按鈕文字 ——
              $100 硬預算下要先報價，但報價不該讓按鈕變成一串小字。 */}
          <span className="prompts-lang-note">
            {fill(t(COPY.costNote, lang), {
              n: (data.estimated_usd ?? 0).toFixed(2),
            })}
          </span>
        </div>
      </section>
    )
  }

  return (
    <section className="section">
      <div className="section-head">
        <h2>
          <Icon name="screening" size={80} className="section-icon" />
          {t(COPY.title, lang)}
        </h2>
        <button className="btn" onClick={run} disabled={busy}>
          {busy ? t(COPY.running, lang) : t(COPY.rescan, lang)}
        </button>
      </div>

      <p className="prompts-note">{t(COPY.why, lang)}</p>

      {error && <div className="notice warn">{t(COPY.failed, lang) + error}</div>}

      {data.scanned && (
        <>
          <div className="notice">
            <strong>
              {risks.length === 0
                ? t(COPY.clean, lang)
                : fill(t(COPY.found, lang), { n: risks.length, h: high })}
            </strong>
            {data.summary && (
              <div style={{ marginTop: 8, whiteSpace: 'pre-wrap' }}>{data.summary}</div>
            )}
          </div>

          {risks.map((r, i) => (
            <div
              key={`${r.category_id}-${r.line_start}-${i}`}
              className={`screening-card${r.severity === 'high' ? ' high' : ''}`}
            >
              <div className="screening-card-head">
                <strong>{t(r.category, lang)}</strong>
                <span className="section-note">
                  {fill(t(COPY.line, lang), { a: r.line_start, b: r.line_end })} ·{' '}
                  {r.is_dialogue ? t(COPY.dialogue, lang) : t(COPY.narration, lang)} ·{' '}
                  {t(COPY.confidence, lang)}：{t(CONFIDENCE[r.confidence] || CONFIDENCE.medium, lang)}
                </span>
              </div>

              <blockquote className="screening-excerpt">{r.excerpt}</blockquote>

              <div className="screening-note">
                <strong>{t(COPY.why2, lang)}</strong>
                <p>{r.note.why}</p>
                <strong>{t(COPY.suggestion, lang)}</strong>
                <p>{r.note.suggestion}</p>
                {/* 罐頭建議是三語的，模型寫的那兩段不是 —— 兩者分開顯示 */}
                {r.general_advice && t(r.general_advice, lang) && (
                  <p className="prompts-lang-note">{t(r.general_advice, lang)}</p>
                )}
                {r.mute_retry_applicable && (
                  <p className="prompts-lang-note">{t(COPY.muteHint, lang)}</p>
                )}
              </div>
            </div>
          ))}

          {/* 免責不是小字免責聲明，是這一關的核心事實：掃過仍可能被擋 */}
          {data.disclaimer && (
            <p className="prompts-lang-note">{t(data.disclaimer, lang)}</p>
          )}
        </>
      )}
    </section>
  )
}
