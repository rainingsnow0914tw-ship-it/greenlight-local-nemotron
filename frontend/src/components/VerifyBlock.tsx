/**
 * 一鏡的本機查證＋診斷（verify/）：鏡頭卡上「生完之後系統查了什麼、哪裡沒過、為什麼、怎麼修」。
 *
 * 這一塊就是賣點本身攤開給人看：不是模型說「我覺得很好」，是程式量的數字（聽寫相似度、開口時間、講完後留幾秒），
 * 量不出來的（嘴有沒有動）就把講話那幾秒的臉截給人看、讓人點，點的那一下也變成紀錄；
 * 沒過的時候 agent 只能從症狀庫挑原因、而且要引用證據，修法附實測出處與提示詞改前改後。
 */
import { useState } from 'react'
import { send, type Lang } from '../api'

type Check = { id: string; level: 'pass' | 'warn' | 'fail' | 'look'; value: number | null; zh: string; en: string; ja: string }
// 診斷裡給人看的字是 {zh, en}（舊紀錄是純字串）：照畫面語言挑，日文沒有就退回英文
type Tr = string | Record<string, string>
type TrList = string[] | Record<string, string[]>
const L = (v: Tr | undefined, lang: Lang): string =>
  !v ? '' : typeof v === 'string' ? v : (v[lang] ?? v.en ?? v.zh ?? '')
const LL = (v: TrList | undefined, lang: Lang): string[] =>
  !v ? [] : Array.isArray(v) ? v : (v[lang] ?? v.en ?? v.zh ?? [])
type Pick = {
  symptom_id: string; title: Record<string, string>; cause: Tr; action: string; fix: Tr; fix_detail: Tr
  new_text: string; evidence: TrList; reasoning: Tr; example?: { before: string; after: string }; source?: string
  source_en?: string; applied?: Tr; by?: 'agent' | 'rule'; tradeoff?: Tr; regenerate?: boolean
}
export type Verify = { checks?: Check[]; images?: Record<string, string>; marks?: string[]; passed?: boolean; heard?: string; trim_head?: number }
export type Diagnosis = {
  picks?: Pick[]; unreviewed?: string; model?: string; dropped?: number; chosen?: number
  current?: { performance: string; line: string }
}

const T = {
  zh: {
    title: '生完查證（本機程式量的）', passed: '查證通過', failed: '查證沒過', look: '要人看',
    mouth: '講話時段的臉', grid: '20 格', saw: '你看到的是：', marks: { 嘴沒動: '嘴沒動', 嘴有動: '嘴有動', 動作重複: '動作重複', 語言不對: '語言不對' },
    dx: '診斷（從症狀庫挑、要引用證據）', cause: '真因', fix: '修法', evidence: '引用的證據', source: '實測出處',
    before: '改前', after: '改後', apply: '套用這個修法', applied: '已套用：', retryHint: '套用後按「重拍」就會用改過的設定',
    unreviewed: '（AI 沒判成，這是程式對號入座的結果）', dropped: (n: number) => `丟掉 ${n} 條沒引用證據的診斷`, why: 'agent 的判斷',
    options: (n: number) => `有 ${n} 種修法，選一個`, byAgent: 'agent 建議', byRule: '程式比對到（agent 沒挑）', cost: '代價',
    regen: '要重生', noRegen: '不用重生', newPerf: '新的表演句（可以改）', newLine: '新的台詞（可以改）', notChosen: '沒選這個',
  },
  en: {
    title: 'Post-render checks (measured locally)', passed: 'Checks passed', failed: 'Checks failed', look: 'needs a look',
    mouth: 'Face while speaking', grid: '20 frames', saw: 'What you see:', marks: { 嘴沒動: 'mouth still', 嘴有動: 'mouth moves', 動作重複: 'action repeats', 語言不對: 'wrong language' },
    dx: 'Diagnosis (picked from the symptom library, with evidence)', cause: 'Cause', fix: 'Fix', evidence: 'Evidence', source: 'Proven in',
    before: 'Before', after: 'After', apply: 'Apply this fix', applied: 'Applied: ', retryHint: 'Then press Retake — it uses the fixed settings',
    unreviewed: '(the AI did not return a cited pick — this is the rule-based match)', dropped: (n: number) => `dropped ${n} uncited pick(s)`, why: "Agent's reasoning",
    options: (n: number) => `${n} possible fixes — pick one`, byAgent: 'suggested by the agent', byRule: 'matched by the rules (the agent did not pick it)', cost: 'Cost',
    regen: 'needs a re-render', noRegen: 'no re-render', newPerf: 'New performance (editable)', newLine: 'New line (editable)', notChosen: 'not chosen',
  },
  ja: {
    title: '生成後の検証（ローカルで計測）', passed: '検証合格', failed: '検証不合格', look: '目視が必要',
    mouth: '話している間の顔', grid: '20 コマ', saw: '見えたのは：', marks: { 嘴沒動: '口が動かない', 嘴有動: '口が動く', 動作重複: '動作の繰り返し', 語言不對: '言語が違う' },
    dx: '診断（症状ライブラリから・根拠つき）', cause: '原因', fix: '修正', evidence: '根拠', source: '実証', before: '修正前', after: '修正後',
    apply: 'この修正を適用', applied: '適用済み：', retryHint: '適用後「撮り直し」で修正済みの設定を使います',
    unreviewed: '（AI の判断なし：ルールによる照合結果）', dropped: (n: number) => `根拠のない診断 ${n} 件を破棄`, why: 'エージェントの判断',
    options: (n: number) => `修正案が ${n} つ：一つ選んでください`, byAgent: 'エージェントの提案', byRule: 'ルールで一致（エージェント未選択）', cost: '代償',
    regen: '再生成が必要', noRegen: '再生成不要', newPerf: '新しい演技文（編集可）', newLine: '新しいセリフ（編集可）', notChosen: '未選択',
  },
}

/** 套用一個修法：改表演句／改台詞的要先讓人看（可改）新的那一句，再套用。 */
function PickAction({ t, busy, editable, prefill, label, onApply }: {
  t: (typeof T)['zh']; busy: boolean; editable: boolean; prefill: string; label: string; onApply: (text: string) => void
}) {
  const [text, setText] = useState(prefill)
  return (
    <div className="verify-apply">
      {editable && (
        <label className="line-edit">{label}
          <textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} />
        </label>
      )}
      <div className="prod-actions">
        <button className="btn primary small" disabled={busy || (editable && !text.trim())} onClick={() => onApply(text)}>{t.apply}</button>
        <span className="prompts-lang-note">{t.retryHint}</span>
      </div>
    </div>
  )
}

const media = (projectId: string, uri?: string) => (uri ? `/api/projects/${projectId}/media/${uri.split('/').pop()}/raw` : '')

export function VerifyBlock({ lang, projectId, order, shotId, verify, diagnosis, onChanged }: {
  lang: Lang; projectId: string; order: number; shotId: string; verify?: Verify; diagnosis?: Diagnosis; onChanged: () => void
}) {
  const t = T[lang]
  const sep = lang === 'en' ? ': ' : '：'
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  if (!verify || !verify.checks) return null
  const checks = verify.checks
  const base = `/api/projects/${projectId}/segments/${order}/clips/${shotId}`
  const post = async (path: string, body: object) => {
    setBusy(true)
    setErr('')
    try {
      await send(`${base}/${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      onChanged()
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const icon = (l: Check['level']) => (l === 'pass' ? '✓' : l === 'fail' ? '✗' : l === 'warn' ? '⚠' : '👁')

  return (
    <div className="verify-block">
      <div className="verify-head">
        <strong>{t.title}</strong>
        <span className={`badge ${verify.passed ? 'done' : 'over'}`}>{verify.passed ? t.passed : t.failed}</span>
      </div>
      {err && <div className="notice warn">{err}</div>}
      <ul className="verify-checks">
        {checks.map((c, i) => <li key={i} className={`v-${c.level}`}><span className="v-icon">{icon(c.level)}</span>{c[lang] || c.zh}</li>)}
      </ul>

      {(verify.images?.mouth || verify.images?.grid) && (
        <div className="verify-images">
          {verify.images?.mouth && <a href={media(projectId, verify.images.mouth)} target="_blank" rel="noreferrer">
            <img src={media(projectId, verify.images.mouth)} alt={t.mouth} /><span>{t.mouth}</span></a>}
          {verify.images?.grid && <a href={media(projectId, verify.images.grid)} target="_blank" rel="noreferrer">
            <img src={media(projectId, verify.images.grid)} alt={t.grid} /><span>{t.grid}</span></a>}
        </div>
      )}
      {checks.some((c) => c.level === 'look') && (
        <div className="verify-marks">
          <span>{t.saw}</span>
          {(Object.keys(t.marks) as (keyof typeof t.marks)[]).map((m) => (
            <button key={m} className={`btn chip small ${verify.marks?.includes(m) ? 'on' : ''}`} disabled={busy}
              onClick={() => void post('observe', { mark: m })}>{t.marks[m]}</button>
          ))}
        </div>
      )}

      {diagnosis?.picks && diagnosis.picks.length > 0 && (
        <div className="verify-dx">
          <strong>{t.dx}</strong>
          {diagnosis.unreviewed && <span className="section-note"> {t.unreviewed}</span>}
          {(diagnosis.dropped ?? 0) > 0 && <span className="section-note"> · {t.dropped(diagnosis.dropped!)}</span>}
          {diagnosis.picks.length > 1 && <div className="verify-options-head">{t.options(diagnosis.picks.length)}</div>}
          <div className={diagnosis.picks.length > 1 ? 'verify-options' : ''}>
            {diagnosis.picks.map((p, i) => {
              const chosen = diagnosis.chosen
              const off = chosen !== undefined && chosen !== i
              const editable = p.action === 'edit_performance' || p.action === 'edit_line'
              const prefill = p.new_text || (p.action === 'edit_line' ? diagnosis.current?.line : diagnosis.current?.performance) || ''
              return (
                <div className={`verify-pick${off ? ' off' : ''}${chosen === i ? ' chosen' : ''}`} key={i}>
                  <div className="verify-pick-title">
                    {diagnosis.picks!.length > 1 && <span className="verify-letter">{String.fromCharCode(65 + i)}</span>}
                    {p.symptom_id} · {p.title[lang] || p.title.zh}
                  </div>
                  <div className="verify-tags">
                    <span className="tag">{p.by === 'rule' ? t.byRule : t.byAgent}</span>
                    <span className={`tag ${p.regenerate ? 'guess' : 'good'}`}>{p.regenerate ? t.regen : t.noRegen}</span>
                  </div>
                  <div><b>{t.cause}{sep}</b>{L(p.cause, lang)}</div>
                  {L(p.reasoning, lang) && <div><b>{t.why}{sep}</b>{L(p.reasoning, lang)}</div>}
                  <div><b>{t.fix}{sep}</b>{L(p.fix, lang)}{L(p.fix_detail, lang) ? ` — ${L(p.fix_detail, lang)}` : ''}</div>
                  {L(p.tradeoff, lang) && <div><b>{t.cost}{sep}</b>{L(p.tradeoff, lang)}</div>}
                  <div className="section-note"><b>{t.evidence}{sep}</b>{LL(p.evidence, lang).join(lang === 'en' ? '; ' : '、')}</div>
                  {p.example && (
                    <div className="verify-example">
                      <div><span className="tag guess">{t.before}</span><code>{p.example.before}</code></div>
                      <div><span className="tag good">{t.after}</span><code>{p.example.after}</code></div>
                    </div>
                  )}
                  {p.source && <div className="section-note"><b>{t.source}{sep}</b>{lang !== 'zh' && p.source_en ? p.source_en : p.source}</div>}
                  {p.applied ? <div className="notice">{t.applied}{L(p.applied, lang)}</div> : off ? <div className="section-note">{t.notChosen}</div> : (
                    <PickAction t={t} busy={busy} editable={editable} prefill={prefill}
                      label={p.action === 'edit_line' ? t.newLine : t.newPerf}
                      onApply={(text) => void post('fix', editable ? { pick: i, text } : { pick: i })} />
                  )}
                </div>
              )
            })}
          </div>
          {err && <div className="notice warn">{err}</div>}
        </div>
      )}
    </div>
  )
}
