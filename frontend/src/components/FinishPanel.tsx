/**
 * 成片流程（services/finish.py）：剪頭 → 放大 → 轉場 → 接片 → 燒字幕。一個按鈕，一路顯示做到哪。
 * 每一步都照作者本機影片管線實測過的做法（放大的圖、混合比例、轉場「不吃口白」、字幕照聽寫時間）。
 */
import { useEffect, useRef, useState } from 'react'
import { makePollGate } from '../pollGate'
import { send, type Lang } from '../api'

type LogEntry = { t: number; kind: string; [k: string]: unknown }
type State = { status: string | null; log: LogEntry[]; outputs: string[]; shots_ready: number; flash?: { shot_id: string; on: boolean }[] }

const T = {
  zh: {
    title: '成片：剪頭 → 放大 → 轉場 → 接片 → 燒字幕', start: '開始成片', restart: '重新成片', subsOnly: '只重燒字幕（不重新放大）', running: '進行中', done: '完成', failed: '失敗',
    steps: ['剪頭', '放大', '轉場', '接片', '燒字幕', '完成'], none: '還沒有生好的鏡頭。', ready: (n: number) => `${n} 鏡可以接`,
    lead: '收下的鏡依分鏡順序：查證量到開頭亂語的先剪掉 → 放大到 1080p → 同一場硬切、換場與頭尾淡出入（只淡在沒講話的地方）→ 接起來 → 學語言模式再燒學習字幕（時間照聽寫量到的開口與講完）。',
    log: {
      start: (e: LogEntry) => `開始：${e.shots} 鏡 → ${e.size}，放大用 ${e.upscale === 'model' ? (String(e.model).includes('anime') ? 'RealESRGAN 動畫模型' : 'RealESRGAN 寫實模型') : 'lanczos'}${e.why_lanczos ? (String(e.why_lanczos).startsWith('fast') ? '（快速：寫實片畫質差不多、快幾百倍）' : '（沒有放大模型）') : ''}`,
      trim: (e: LogEntry) => `${e.shot}：剪掉開頭 ${e.seconds} 秒`,
      upscale: (e: LogEntry) => `${e.shot}：放大（${e.index}/${e.of}）`,
      reuse: (e: LogEntry) => `${e.shot}：沒有改過，沿用上次剪好、放大好的（${e.index}/${e.of}）`,
      retrim: (e: LogEntry) => `${e.shot}：重聽一次，台詞前還有「${e.extra}」→ 再剪掉 ${e.seconds} 秒`,
      transition: (e: LogEntry) => `${e.shot}：${Number(e.fade_in) > 0 ? `淡入 ${e.fade_in} 秒 ` : ''}${Number(e.fade_out) > 0 ? `淡出 ${e.fade_out} 秒` : ''}`,
      skip: (e: LogEntry) => `${e.shot}：跳過（找不到檔案）`,
      assembled: (e: LogEntry) => `接片完成：${e.shots} 鏡、${e.seconds} 秒`,
      subtitles: (e: LogEntry) => `燒字幕：${e.lang}（${e.lines} 句）`,
      done: (e: LogEntry) => `完成：${e.seconds} 秒`, failed: (e: LogEntry) => `停下來了：${e.error}`,
      flash: (e: LogEntry) => `${e.shot}：${e.flash_out ? '結尾閃白' : '從閃白開始'}`,
    },
    flashTitle: '閃白（時間過了一下的地方）', flashLead: '同一場戲裡位置跳了、或情緒前後接不上（中間的過程沒拍）→ 在那一鏡前面閃一下白。一句接一句的對話、接幀的鏡不用。下次成片生效。', flashBefore: (id: string) => `${id} 前`,
    dl: { master: '成片（無字幕）' } as Record<string, string>, dlStudy: (k: string) => `學習字幕版 ${k}`,
    langs: { ja: '日', zh: '中', en: '英', ko: '韓' } as Record<string, string>, pair: (a: string, b: string) => `${a}${b}雙字幕版`,
    watch: '看成品', logTitle: '處理紀錄', download: '下載',
  },
  en: {
    title: 'Finish: trim → upscale → transitions → assemble → subtitles', start: 'Finish this part', restart: 'Finish again', subsOnly: 'Re-burn subtitles only (no re-upscale)', running: 'Running', done: 'Done', failed: 'Failed',
    steps: ['Trim', 'Upscale', 'Transitions', 'Assemble', 'Subtitles', 'Done'], none: 'No generated shots yet.', ready: (n: number) => `${n} shot(s) ready`,
    lead: 'Approved shots in shot-list order: gibberish heads (measured by the checks) are trimmed → upscaled to 1080p → hard cuts inside a scene, fades between scenes and at the ends (only over silence, never over a line) → joined → in study mode, study subtitles timed to when each line is actually spoken.',
    log: {
      start: (e: LogEntry) => `Start: ${e.shots} shots → ${e.size}, upscale with ${e.upscale === 'model' ? (String(e.model).includes('anime') ? 'the RealESRGAN anime model' : 'the RealESRGAN photoreal model') : 'lanczos'}${e.why_lanczos ? (String(e.why_lanczos).startsWith('fast') ? ' (fast: on photoreal footage it looks almost the same and is hundreds of times quicker)' : ' (no upscale model installed)') : ''}`,
      trim: (e: LogEntry) => `${e.shot}: trimmed ${e.seconds}s off the head`, upscale: (e: LogEntry) => `${e.shot}: upscaled (${e.index}/${e.of})`,
      reuse: (e: LogEntry) => `${e.shot}: unchanged — reusing the trimmed, upscaled copy from last time (${e.index}/${e.of})`,
      retrim: (e: LogEntry) => `${e.shot}: listened again — “${e.extra}” still before the line, trimmed ${e.seconds}s more`,
      transition: (e: LogEntry) => `${e.shot}: ${Number(e.fade_in) > 0 ? `fade in ${e.fade_in}s ` : ''}${Number(e.fade_out) > 0 ? `fade out ${e.fade_out}s` : ''}`,
      skip: (e: LogEntry) => `${e.shot}: skipped (file missing)`, assembled: (e: LogEntry) => `Assembled: ${e.shots} shots, ${e.seconds}s`,
      subtitles: (e: LogEntry) => `Subtitles: ${e.lang} (${e.lines} lines)`, done: (e: LogEntry) => `Done: ${e.seconds}s`, failed: (e: LogEntry) => `Stopped: ${e.error}`,
      flash: (e: LogEntry) => `${e.shot}: ${e.flash_out ? 'flash to white at the end' : 'starts from a white flash'}`,
    },
    flashTitle: 'White flash (a moment has passed)', flashLead: 'Within one scene, where someone has moved or the emotion jumps (the in-between was not filmed), flash to white before that shot. Not for back-and-forth lines or chained shots. Applies the next time you finish.', flashBefore: (id: string) => `before ${id}`,
    dl: { master: 'Film (no subtitles)' } as Record<string, string>, dlStudy: (k: string) => `Study subtitles ${k}`,
    langs: { ja: 'Japanese', zh: 'Chinese', en: 'English', ko: 'Korean' } as Record<string, string>, pair: (a: string, b: string) => `${a}–${b} subtitles`,
    watch: 'Watch the result', logTitle: 'Processing log', download: 'Download',
  },
  ja: {
    title: '仕上げ：頭切り → アップスケール → トランジション → 結合 → 字幕', start: '仕上げを始める', restart: 'もう一度仕上げる', subsOnly: '字幕だけ焼き直す（再アップスケールなし）', running: '進行中', done: '完了', failed: '失敗',
    steps: ['頭切り', 'アップスケール', 'トランジション', '結合', '字幕', '完了'], none: '生成済みのカットがありません。', ready: (n: number) => `${n} カット結合可能`,
    lead: '採用したカットを絵コンテ順に：検証で測った冒頭の意味不明な声を切る → 1080p にアップスケール → シーン内はカット、シーン間と前後はフェード（セリフのない所だけ）→ 結合 → 語学モードでは学習字幕（実際に話した時間に合わせる）。',
    log: {
      start: (e: LogEntry) => `開始：${e.shots} カット → ${e.size}、${e.upscale === 'model' ? (String(e.model).includes('anime') ? 'RealESRGAN アニメモデル' : 'RealESRGAN 実写モデル') : 'lanczos'}で拡大${e.why_lanczos ? (String(e.why_lanczos).startsWith('fast') ? '（高速：実写では画質がほぼ同じで数百倍速い）' : '（拡大モデル未インストール）') : ''}`,
      trim: (e: LogEntry) => `${e.shot}：冒頭 ${e.seconds} 秒カット`, upscale: (e: LogEntry) => `${e.shot}：拡大（${e.index}/${e.of}）`,
      reuse: (e: LogEntry) => `${e.shot}：変更なし、前回カット・拡大済みのものを使用（${e.index}/${e.of}）`,
      retrim: (e: LogEntry) => `${e.shot}：聞き直し、セリフの前に「${e.extra}」→ さらに ${e.seconds} 秒カット`,
      transition: (e: LogEntry) => `${e.shot}：${Number(e.fade_in) > 0 ? `フェードイン ${e.fade_in} 秒 ` : ''}${Number(e.fade_out) > 0 ? `フェードアウト ${e.fade_out} 秒` : ''}`,
      skip: (e: LogEntry) => `${e.shot}：スキップ（ファイルなし）`, assembled: (e: LogEntry) => `結合完了：${e.shots} カット、${e.seconds} 秒`,
      subtitles: (e: LogEntry) => `字幕：${e.lang}（${e.lines} 行）`, done: (e: LogEntry) => `完了：${e.seconds} 秒`, failed: (e: LogEntry) => `停止：${e.error}`,
      flash: (e: LogEntry) => `${e.shot}：${e.flash_out ? '最後に白フラッシュ' : '白フラッシュから始まる'}`,
    },
    flashTitle: '白フラッシュ（少し時間が経ったところ）', flashLead: '同じシーンの中で位置が飛んだり、感情がつながらない（途中を撮っていない）ところは、そのカットの前で白く光らせる。セリフの掛け合いやつないだカットには使わない。次の仕上げから反映。', flashBefore: (id: string) => `${id} の前`,
    dl: { master: '完成版（字幕なし）' } as Record<string, string>, dlStudy: (k: string) => `学習字幕版 ${k}`,
    langs: { ja: '日', zh: '中', en: '英', ko: '韓' } as Record<string, string>, pair: (a: string, b: string) => `${a}${b}二言語字幕版`,
    watch: '完成品を見る', logTitle: '処理の記録', download: 'ダウンロード',
  },
}

const STEP_OF: Record<string, number> = { start: 0, trim: 0, upscale: 1, reuse: 1, retrim: 1, transition: 2, flash: 2, assembled: 3, subtitles: 4, done: 5 }

export function FinishPanel({ lang, projectId, order }: { lang: Lang; projectId: string; order: number }) {
  const t = T[lang]
  const [st, setSt] = useState<State | null>(null)
  const statusRef = useRef<string | null>(null)   // 最近一次的狀態：沒在跑就放慢輪詢（pollGate）
  const [err, setErr] = useState('')
  const [pick, setPick] = useState<string | null>(null)
  const alive = useRef(true)
  const base = `/api/projects/${projectId}/segments/${order}/finish`
  const poll = async () => {
    const r = await fetch(base)
    if (r.ok && alive.current) { const s = await r.json(); statusRef.current = s.status; setSt(s) }
  }
  useEffect(() => {
    alive.current = true
    void poll()
    const gate = makePollGate()
    const id = setInterval(() => { if (gate(statusRef.current)) void poll() }, 3000)
    return () => { alive.current = false; clearInterval(id) }
  }, [projectId, order])
  if (!st) return null
  // 成品的名字：ja-zh → 日中雙字幕版／Japanese–Chinese subtitles（不要讓人看到 ja-zh 這種代碼）
  const outName = (k: string) => {
    if (t.dl[k]) return t.dl[k]
    const [a, b] = k.split('-')
    return a && b && t.langs[a] && t.langs[b] ? t.pair(t.langs[a], t.langs[b]) : t.dlStudy(k)
  }
  // 預設先放雙字幕版：學語言模式的成品就是它
  const outs = [...st.outputs.filter((k) => k !== 'master'), ...st.outputs.filter((k) => k === 'master')]   // 雙字幕版在前、無字幕在後
  const cur = pick && st.outputs.includes(pick) ? pick : (outs[0])
  const done = st.status === 'done' && st.outputs.length > 0
  const last = st.log[st.log.length - 1]
  const step = last ? (STEP_OF[last.kind] ?? 0) : -1
  // subsOnly：改了台詞讀音／字幕之後，拿上次的母片重燒就好，不用再放大一次（後端 finish.start subs_only）
  const start = async (subsOnly = false) => {
    setErr('')
    try {
      await send(subsOnly ? `${base}?subs_only=true` : base, { method: 'POST' })
    } catch (e) {
      setErr((e as Error).message)
    }
    void poll()
  }

  const toggleFlash = async (id: string, on: boolean) => {
    setErr('')
    try {
      await send(`${base}/flash/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ on }) })
    } catch (e) {
      setErr((e as Error).message)
    }
    void poll()
  }

  return (
    <section className="section">
      <div className="section-head">
        <h2>{t.title}</h2>
        {st.status && <span className={`badge ${st.status === 'done' ? 'done' : (st.status === 'failed' || st.status === 'interrupted') ? 'over' : 'in-progress'}`}>
          {st.status === 'done' ? t.done : (st.status === 'failed' || st.status === 'interrupted') ? t.failed : t.running}</span>}
      </div>
      {err && <div className="notice warn">{err}</div>}
      <p className="lang-lead">{t.lead}</p>
      {st.flash && st.flash.length > 0 && (
        <details open={st.flash.some((f) => f.on)}>
          <summary>{t.flashTitle}</summary>
          <p className="section-note">{t.flashLead}</p>
          <div className="actions">
            {st.flash.map((f) => (
              <button key={f.shot_id} className={`btn small ${f.on ? 'primary' : 'ghost'}`} disabled={st.status === 'running' || projectId.startsWith('p-sample-')}
                onClick={() => void toggleFlash(f.shot_id, !f.on)}>{t.flashBefore(f.shot_id)}</button>
            ))}
          </div>
        </details>
      )}
      {st.log.length > 0 && (
        <ol className="direction-steps">
          {t.steps.map((label, i) => <li key={label} className={i < step || st.status === 'done' ? 'done' : i === step ? 'current' : ''}>{label}</li>)}
        </ol>
      )}
      {done && cur && (
        <div className="finish-player">
          <h3>{t.watch}</h3>
          <div className="actions">
            {outs.map((k) => (
              <button key={k} className={`btn small ${k === cur ? 'primary' : 'ghost'}`} onClick={() => setPick(k)}>{outName(k)}</button>
            ))}
          </div>
          <video key={cur} controls preload="metadata" src={`${base}/${cur}`} style={{ width: '100%', borderRadius: 8, background: '#000' }} />
        </div>
      )}
      {st.log.length > 0 && (() => {
        const list = (
          <ul className="direction-log">
            {st.log.map((e, i) => {
              const f = (t.log as Record<string, (e: LogEntry) => string>)[e.kind]
              return f ? <li key={i} className={e.kind === 'done' ? 'ok' : e.kind === 'failed' ? 'bad' : ''}>{f(e)}</li> : null
            })}
          </ul>
        )
        // 做完了紀錄收起來（成品放前面）；進行中照常展開看進度
        return done ? <details><summary>{t.logTitle}</summary>{list}</details> : list
      })()}
      <div className="actions">
        {st.shots_ready === 0 ? <span className="section-note">{t.none}</span> : st.status !== 'running' && (
          <>
            <button className="btn primary" onClick={() => void start()}>{st.status ? t.restart : t.start}</button>
            {st.outputs.includes('master') && st.outputs.length > 1 && (
              <button className="btn ghost" onClick={() => void start(true)}>{t.subsOnly}</button>
            )}
            <span className="section-note">{t.ready(st.shots_ready)}</span>
          </>
        )}
        {outs.map((k) => (
          <a key={k} className="btn ghost small" href={`${base}/${k}`}>{t.download} · {outName(k)}</a>
        ))}
      </div>
    </section>
  )
}
