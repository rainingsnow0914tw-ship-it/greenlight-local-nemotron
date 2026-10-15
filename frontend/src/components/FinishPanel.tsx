/**
 * 成片流程（services/finish.py）：剪頭 → 放大 → 轉場 → 接片 → 燒字幕。一個按鈕，一路顯示做到哪。
 * 每一步都照作者本機影片管線實測過的做法（放大的圖、混合比例、轉場「不吃口白」、字幕照聽寫時間）。
 */
import { useEffect, useRef, useState } from 'react'
import type { Lang } from '../api'

type LogEntry = { t: number; kind: string; [k: string]: unknown }
type State = { status: string | null; log: LogEntry[]; outputs: string[]; shots_ready: number }

const T = {
  zh: {
    title: '成片：剪頭 → 放大 → 轉場 → 接片 → 燒字幕', start: '開始成片', restart: '重新成片', running: '進行中', done: '完成', failed: '失敗',
    steps: ['剪頭', '放大', '轉場', '接片', '燒字幕', '完成'], none: '還沒有生好的鏡頭。', ready: (n: number) => `${n} 鏡可以接`,
    lead: '收下的鏡依分鏡順序：查證量到開頭亂語的先剪掉 → 放大到 1080p → 同一場硬切、換場與頭尾淡出入（只淡在沒講話的地方）→ 接起來 → 學語言模式再燒學習字幕（時間照聽寫量到的開口與講完）。',
    log: {
      start: (e: LogEntry) => `開始：${e.shots} 鏡 → ${e.size}，放大用 ${e.upscale === 'model' ? (String(e.model).includes('anime') ? 'RealESRGAN 動畫模型' : 'RealESRGAN 寫實模型') : 'lanczos'}${e.why_lanczos ? (String(e.why_lanczos).startsWith('fast') ? '（快速：寫實片畫質差不多、快幾百倍）' : '（沒有放大模型）') : ''}`,
      trim: (e: LogEntry) => `${e.shot}：剪掉開頭 ${e.seconds} 秒`,
      upscale: (e: LogEntry) => `${e.shot}：放大（${e.index}/${e.of}）`,
      transition: (e: LogEntry) => `${e.shot}：${Number(e.fade_in) > 0 ? `淡入 ${e.fade_in} 秒 ` : ''}${Number(e.fade_out) > 0 ? `淡出 ${e.fade_out} 秒` : ''}`,
      skip: (e: LogEntry) => `${e.shot}：跳過（找不到檔案）`,
      assembled: (e: LogEntry) => `接片完成：${e.shots} 鏡、${e.seconds} 秒`,
      subtitles: (e: LogEntry) => `燒字幕：${e.lang}（${e.lines} 句）`,
      done: (e: LogEntry) => `完成：${e.seconds} 秒`, failed: (e: LogEntry) => `停下來了：${e.error}`,
    },
    dl: { master: '成片（無字幕）' } as Record<string, string>, dlStudy: (k: string) => `學習字幕版 ${k}`,
  },
  en: {
    title: 'Finish: trim → upscale → transitions → assemble → subtitles', start: 'Finish this part', restart: 'Finish again', running: 'Running', done: 'Done', failed: 'Failed',
    steps: ['Trim', 'Upscale', 'Transitions', 'Assemble', 'Subtitles', 'Done'], none: 'No generated shots yet.', ready: (n: number) => `${n} shot(s) ready`,
    lead: 'Approved shots in shot-list order: gibberish heads (measured by the checks) are trimmed → upscaled to 1080p → hard cuts inside a scene, fades between scenes and at the ends (only over silence, never over a line) → joined → in study mode, study subtitles timed to when each line is actually spoken.',
    log: {
      start: (e: LogEntry) => `Start: ${e.shots} shots → ${e.size}, upscale with ${e.upscale === 'model' ? (String(e.model).includes('anime') ? 'the RealESRGAN anime model' : 'the RealESRGAN photoreal model') : 'lanczos'}${e.why_lanczos ? ` (${e.why_lanczos})` : ''}`,
      trim: (e: LogEntry) => `${e.shot}: trimmed ${e.seconds}s off the head`, upscale: (e: LogEntry) => `${e.shot}: upscaled (${e.index}/${e.of})`,
      transition: (e: LogEntry) => `${e.shot}: ${Number(e.fade_in) > 0 ? `fade in ${e.fade_in}s ` : ''}${Number(e.fade_out) > 0 ? `fade out ${e.fade_out}s` : ''}`,
      skip: (e: LogEntry) => `${e.shot}: skipped (file missing)`, assembled: (e: LogEntry) => `Assembled: ${e.shots} shots, ${e.seconds}s`,
      subtitles: (e: LogEntry) => `Subtitles: ${e.lang} (${e.lines} lines)`, done: (e: LogEntry) => `Done: ${e.seconds}s`, failed: (e: LogEntry) => `Stopped: ${e.error}`,
    },
    dl: { master: 'Film (no subtitles)' } as Record<string, string>, dlStudy: (k: string) => `Study subtitles ${k}`,
  },
  ja: {
    title: '仕上げ：頭切り → アップスケール → トランジション → 結合 → 字幕', start: '仕上げを始める', restart: 'もう一度仕上げる', running: '進行中', done: '完了', failed: '失敗',
    steps: ['頭切り', 'アップスケール', 'トランジション', '結合', '字幕', '完了'], none: '生成済みのカットがありません。', ready: (n: number) => `${n} カット結合可能`,
    lead: '採用したカットを絵コンテ順に：検証で測った冒頭の意味不明な声を切る → 1080p にアップスケール → シーン内はカット、シーン間と前後はフェード（セリフのない所だけ）→ 結合 → 語学モードでは学習字幕（実際に話した時間に合わせる）。',
    log: {
      start: (e: LogEntry) => `開始：${e.shots} カット → ${e.size}、${e.upscale === 'model' ? (String(e.model).includes('anime') ? 'RealESRGAN アニメモデル' : 'RealESRGAN 実写モデル') : 'lanczos'}で拡大`,
      trim: (e: LogEntry) => `${e.shot}：冒頭 ${e.seconds} 秒カット`, upscale: (e: LogEntry) => `${e.shot}：拡大（${e.index}/${e.of}）`,
      transition: (e: LogEntry) => `${e.shot}：${Number(e.fade_in) > 0 ? `フェードイン ${e.fade_in} 秒 ` : ''}${Number(e.fade_out) > 0 ? `フェードアウト ${e.fade_out} 秒` : ''}`,
      skip: (e: LogEntry) => `${e.shot}：スキップ（ファイルなし）`, assembled: (e: LogEntry) => `結合完了：${e.shots} カット、${e.seconds} 秒`,
      subtitles: (e: LogEntry) => `字幕：${e.lang}（${e.lines} 行）`, done: (e: LogEntry) => `完了：${e.seconds} 秒`, failed: (e: LogEntry) => `停止：${e.error}`,
    },
    dl: { master: '完成版（字幕なし）' } as Record<string, string>, dlStudy: (k: string) => `学習字幕版 ${k}`,
  },
}

const STEP_OF: Record<string, number> = { start: 0, trim: 0, upscale: 1, transition: 2, assembled: 3, subtitles: 4, done: 5 }

export function FinishPanel({ lang, projectId, order }: { lang: Lang; projectId: string; order: number }) {
  const t = T[lang]
  const [st, setSt] = useState<State | null>(null)
  const alive = useRef(true)
  const base = `/api/projects/${projectId}/segments/${order}/finish`
  const poll = async () => {
    const r = await fetch(base)
    if (r.ok && alive.current) setSt(await r.json())
  }
  useEffect(() => {
    alive.current = true
    void poll()
    const id = setInterval(() => void poll(), 3000)
    return () => { alive.current = false; clearInterval(id) }
  }, [projectId, order])
  if (!st) return null
  const last = st.log[st.log.length - 1]
  const step = last ? (STEP_OF[last.kind] ?? 0) : -1
  const start = async () => { await fetch(base, { method: 'POST' }); void poll() }

  return (
    <section className="section">
      <div className="section-head">
        <h2>{t.title}</h2>
        {st.status && <span className={`badge ${st.status === 'done' ? 'done' : st.status === 'failed' ? 'over' : 'in-progress'}`}>
          {st.status === 'done' ? t.done : st.status === 'failed' ? t.failed : t.running}</span>}
      </div>
      <p className="lang-lead">{t.lead}</p>
      {st.log.length > 0 && (
        <ol className="direction-steps">
          {t.steps.map((label, i) => <li key={label} className={i < step || st.status === 'done' ? 'done' : i === step ? 'current' : ''}>{label}</li>)}
        </ol>
      )}
      {st.log.length > 0 && (
        <ul className="direction-log">
          {st.log.map((e, i) => {
            const f = (t.log as Record<string, (e: LogEntry) => string>)[e.kind]
            return f ? <li key={i} className={e.kind === 'done' ? 'ok' : e.kind === 'failed' ? 'bad' : ''}>{f(e)}</li> : null
          })}
        </ul>
      )}
      <div className="actions">
        {st.shots_ready === 0 ? <span className="section-note">{t.none}</span> : st.status !== 'running' && (
          <>
            <button className="btn primary" onClick={() => void start()}>{st.status ? t.restart : t.start}</button>
            <span className="section-note">{t.ready(st.shots_ready)}</span>
          </>
        )}
        {st.outputs.map((k) => (
          <a key={k} className="btn ghost small" href={`${base}/${k}`}>{t.dl[k] ?? t.dlStudy(k)}</a>
        ))}
      </div>
    </section>
  )
}
