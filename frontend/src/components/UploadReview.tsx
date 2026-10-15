/**
 * 上傳後的確認畫面 —— 花錢之前的最後一關。
 *
 * 這一頁存在的唯一理由：**解析是第一個花錢的動作**。
 *    上傳完就自動扣款是不對的。所以這裡把三件事攤開來：
 *      1. 系統讀到了什麼（格式、編碼、字數、章節）
 *      2. 解析要花多少錢（給區間不給假精確的單一數字）
 *      3. 一個明確的「開始」按鈕，跟一個「取消」
 *
 * 章節偵測是**免費**的（純字串比對），所以在付費前就能顯示 ——
 * 讓使用者先確認系統看懂了他的檔案結構，再決定要不要付錢往下走。
 */

import { useState } from 'react'
import type { Lang } from '../api'
import type { Copy } from '../i18n'

export interface UploadResult {
  project_id: string
  title: string
  file: {
    filename: string
    format: string
    encoding: string | null
    page_count: number | null
    char_count: number
    line_count: number
  }
  chapters_detected: number
  chapter_titles: string[]
  continuity_problems: string[]
  parse_estimate: {
    estimated_input_tokens: number
    estimated_output_tokens: number
    estimated_cost_usd: number
    estimated_cost_low_usd: number
    estimated_cost_high_usd: number
    model_label: string
    calibration_note: string
  }
  warnings: string[]
}

interface Props {
  copy: Copy
  lang: Lang
  upload: UploadResult
  onCancel: () => void
  onParsed: (projectId: string) => void
  onError: (msg: string) => void
}

export function UploadReview({ copy, upload, onCancel, onParsed, onError }: Props) {
  const [parsing, setParsing] = useState(false)
  const est = upload.parse_estimate
  const f = upload.file

  const start = async () => {
    setParsing(true)
    onError('')
    try {
      const res = await fetch(`/api/projects/${upload.project_id}/parse`, {
        method: 'POST',
      })
      const body = await res.json()
      if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`)
      onParsed(upload.project_id)
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
      setParsing(false)
    }
  }

  return (
    <>
      <p className="eyebrow">{copy.reviewTitle}</p>
      <h1>{upload.title}</h1>

      <div className="stats">
        <div>
          <div className="stat-label">{copy.fileInfo}</div>
          <div className="stat-value" style={{ fontSize: 16 }}>
            {f.format}
            {f.encoding && <span className="unit">{f.encoding}</span>}
          </div>
        </div>
        <div>
          <div className="stat-label">字元 / chars</div>
          <div className="stat-value">{f.char_count.toLocaleString()}</div>
        </div>
        <div>
          <div className="stat-label">行 / lines</div>
          <div className="stat-value">{f.line_count.toLocaleString()}</div>
        </div>
        {f.page_count != null && (
          <div>
            <div className="stat-label">頁 / pages</div>
            <div className="stat-value">{f.page_count}</div>
          </div>
        )}
      </div>

      {upload.warnings.length > 0 && (
        <div className="notice warn">
          {upload.warnings.map((w, i) => (
            <div key={i}>{w}</div>
          ))}
        </div>
      )}

      {/* 章節偵測 —— 免費，所以放在付費之前 */}
      <section className="section">
        <div className="section-head">
          <h2>{copy.chaptersFound}</h2>
          <span className="section-note">
            {upload.chapters_detected > 0 ? `${upload.chapters_detected} · $0` : '—'}
          </span>
        </div>
        {upload.chapters_detected === 0 ? (
          <p className="lede">{copy.noChapters}</p>
        ) : (
          <ol className="chapter-preview">
            {upload.chapter_titles.map((title, i) => (
              <li key={i}>
                <span>{i + 1}</span>
                <span style={{ minWidth: 0, textAlign: 'left', flex: 1 }}>{title}</span>
              </li>
            ))}
            {upload.chapters_detected > upload.chapter_titles.length && (
              <li>
                <span>…</span>
                <span style={{ textAlign: 'left' }}>
                  +{upload.chapters_detected - upload.chapter_titles.length}
                </span>
              </li>
            )}
          </ol>
        )}
        {upload.continuity_problems.map((p, i) => (
          <div className="notice warn" key={i}>
            {p}
          </div>
        ))}
      </section>

      {/* 費用 */}
      <section className="section">
        <div className="section-head">
          <h2>{copy.parseCost}</h2>
          <span className="section-note">{est.model_label}</span>
        </div>

        <div className="cost-range">
          ${est.estimated_cost_low_usd.toFixed(2)} – $
          {est.estimated_cost_high_usd.toFixed(2)}
          <span className="mid">
            ~${est.estimated_cost_usd.toFixed(2)} ·{' '}
            {est.estimated_input_tokens.toLocaleString()} in /{' '}
            {est.estimated_output_tokens.toLocaleString()} out
          </span>
        </div>

        <div className="notice">
          <strong>{copy.parseCostNote}</strong>
          <div style={{ marginTop: 6, fontSize: 12 }}>{est.calibration_note}</div>
        </div>

        <div className="actions">
          <button className="btn primary" onClick={start} disabled={parsing}>
            {parsing ? copy.parseWaiting : copy.parseButton}
          </button>
          {!parsing && (
            <button className="btn ghost" onClick={onCancel}>
              {copy.cancel}
            </button>
          )}
        </div>
      </section>
    </>
  )
}
