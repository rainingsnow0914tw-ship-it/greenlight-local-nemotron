/**
 * 生成前關卡擋下一鏡時，錯誤框裡的「讓 Nemotron 修這一鏡」。
 *
 * 為什麼要有：關卡說「先改分鏡再生」，但 app 沒有改單一鏡的地方，唯一的路是整段重導
 * （第三世試跑：71 分鐘）。後端 POST .../shots/{id}/fix 只改這一鏡（必要時連前後鄰鏡），
 * 重跑關卡、變好才寫回；約 30 秒～2 分鐘。
 */
import { useState } from 'react'
import type { Lang } from '../api'

type Result = { status: 'fixed' | 'partial' | 'failed'; changed: string[]; removed: string[]; errors_before: number; errors_after: number }

const T = {
  button: { zh: '讓 Nemotron 修這一鏡', en: 'Let Nemotron fix this shot', ja: 'Nemotron にこのカットを直させる' },
  busy: { zh: 'Nemotron 正在改這一鏡（約 30 秒～2 分鐘）……', en: 'Nemotron is rewriting this shot (about 30 s – 2 min)…', ja: 'Nemotron がこのカットを書き直しています（30秒〜2分）……' },
  waive: { zh: '照樣生成（我看過了，這條不適用）', en: 'Generate anyway (I checked — the rule does not apply)', ja: 'このまま生成（確認済み、このルールは当てはまらない）' },
  waived: { zh: '已標記照樣生成，可以繼續生成了。', en: 'Marked to generate anyway — you can continue generating.', ja: 'このまま生成するよう設定しました。生成を続けられます。' },
  failed: { zh: '改了三次都沒有變好，分鏡沒動。可以再按一次，或整段重新導演。', en: 'Three tries did not improve it; the storyboard is unchanged. Try again or re-direct the part.', ja: '3回試しても良くならず、絵コンテは変更していません。もう一度押すか、パートごと演出し直してください。' },
}

export const PREFLIGHT_RE = /(\S+) 沒過生成前關卡/

export function FixShotButton({ projectId, order, error, lang, onFixed }: {
  projectId: string; order: number; error: string; lang: Lang; onFixed: (note: string) => void
}) {
  const shotId = error.match(PREFLIGHT_RE)?.[1]
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  if (!shotId) return null

  const fix = async () => {
    setBusy(true)
    setMsg('')
    try {
      const r = await fetch(`/api/projects/${projectId}/segments/${order}/shots/${encodeURIComponent(shotId)}/fix`, { method: 'POST' })
      const body = await r.json()
      if (!r.ok) throw new Error(typeof body.detail === 'string' ? body.detail : r.statusText)
      const res = body as Result
      if (res.status === 'failed') {
        setMsg(T.failed[lang])
        return
      }
      const changed = res.changed.join(', ') || '—'
      const removed = res.removed.length ? res.removed.join(', ') : ''
      onFixed(lang === 'en'
        ? `✓ ${shotId}: changed ${changed}${removed ? `; merged away ${removed}` : ''} (errors ${res.errors_before} → ${res.errors_after}).`
        : lang === 'ja'
          ? `✓ ${shotId}：${changed} を修正${removed ? `、${removed} を統合` : ''}（エラー ${res.errors_before} → ${res.errors_after}）。`
          : `✓ ${shotId}：改了 ${changed}${removed ? `、併掉 ${removed}` : ''}（錯誤 ${res.errors_before} → ${res.errors_after}）。`)
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  // 修不好（例如貓叫被當成太短的台詞、又沒有能併的鄰鏡）→ 人看過可以照樣生成，不然批次卡在這一鏡、後面全部生不了
  const waive = async () => {
    setBusy(true)
    try {
      const r = await fetch(`/api/projects/${projectId}/segments/${order}/shots/${encodeURIComponent(shotId)}/waive`, { method: 'POST' })
      if (!r.ok) throw new Error(r.statusText)
      onFixed(`✓ ${shotId}：${T.waived[lang]}`)
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ marginTop: 10, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
      <button className="btn" onClick={() => void fix()} disabled={busy}>{busy ? T.busy[lang] : T.button[lang]}</button>
      <button className="btn ghost" onClick={() => void waive()} disabled={busy}>{T.waive[lang]}</button>
      {msg && <div style={{ flexBasis: '100%' }}>{msg}</div>}
    </div>
  )
}
