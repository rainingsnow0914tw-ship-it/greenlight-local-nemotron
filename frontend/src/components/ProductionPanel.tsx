/**
 * 第 7–9 關：試拍 → 批次生成 → 接片與字幕。
 *
 * 這三關是連續的一條路，所以放在同一個面板 ——
 * 使用者不需要知道它們在後端是三組不同的 endpoint。
 *
 * 這一整段的設計來自兩個產品決定：
 *
 *   逐鏡確認：「做一個檢查過沒問題，再生成下一個…免得浪費錢。」
 *     所以「生成下一鏡」在有鏡頭等確認時是**鎖住的**。
 *     不鎖的話就退化成「全部生完再一起看」，跟省錢的目的相反。
 *
 *   字幕外掛：「外掛之後隨時都可以改成多語言版本就對了。」
 *     影片只生一次，字幕想出幾種語言都行。
 *
 * 追加兩件事：
 *
 *   一鍵模式：「兩種都有…有些人懶得弄的話就一鍵也可以。」
 *     逐鏡按鈕保留，旁邊多一個一鍵。一鍵**不自動通過** ——
 *     只是連續生成，最後仍然要人過目裁決。
 *
 *   字幕燒進影片：「裡面可以選合成字幕部分，要選中文、英文、日文。」
 *     在這之前只能下載 .srt 自己去合成，那正是這個產品要消滅的那一層。
 */

import { Icon } from './Icons'
import { sysText } from '../sysText'
import { VerifyBlock } from './VerifyBlock'
import { AiText } from './AiText'
import { PREFLIGHT_RE } from './FixShotButton'
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  videoApi,
  t,
  type BatchState,
  type ClipRecord,
  type Lang,
  type Localized,
  type TestShotState,
} from '../api'
import { LANGS, type Copy } from '../i18n'

interface Props {
  copy: Copy
  lang: Lang
  projectId: string
  order: number
  onError: (msg: string) => void
  /** 選角沒完成時，要能一鍵跳過去 —— 擋住的同時得告訴他去哪。 */
  onOpenCasting: () => void
}

/** 這一版新增的文案。放這裡而不是 i18n.ts，是因為它們只有這個面板用得到。 */
const EXTRA: Record<string, Localized> = {
  autoRun: { zh: '一鍵生成剩下全部', en: 'Generate all remaining', ja: '残り全部を一括生成' },
  autoHint: {
    zh: '連續生成，中途可以停。生完仍然要你逐鏡過目 —— 一鍵不等於自動通過。',
    en: 'Runs continuously, stoppable. You still review each shot — auto ≠ auto-approve.',
    ja: '連続生成（中断可）。生成後は一つずつ確認します —— 一括≠自動承認。',
  },
  autoRunning: { zh: '生成中', en: 'Generating', ja: '生成中' },
  autoStop: { zh: '停下來', en: 'Stop', ja: '中断' },
  autoStopped: { zh: '已停止。已生成的鏡頭都保留著。', en: 'Stopped. Generated shots are kept.', ja: '停止しました。生成済みは保持されます。' },
  autoShot: { zh: '第 {n}/{total} 鏡', en: 'Shot {n}/{total}', ja: 'ショット {n}/{total}' },
  castingGateTitle: {
    zh: '要先選角才能開始生成',
    en: 'Casting must be locked before generating',
    ja: 'まずキャスティングを確定してください',
  },
  castingGateWhy: {
    zh: '沒有參考圖的角色，每一鏡都會長得不一樣。選角一輪約 $0.06，重拍一鏡要 $0.40 —— 這一關是替你省錢，不是擋你。',
    en: 'Without a locked reference, a character looks different in every shot. A casting round takes seconds; re-shooting one shot takes minutes.',
    ja: '参照画像がないと、同じ人物が毎ショット別人になります。キャスティングは1回約 $0.06、撮り直しは1ショット $0.40 です。',
  },
  castingGateGo: { zh: '去選角', en: 'Go to casting', ja: 'キャスティングへ' },
  castingPending: { zh: '還沒選定', en: 'Not yet cast', ja: '未確定' },
  burnTitle: { zh: '把字幕燒進影片', en: 'Burn subtitles into video', ja: '字幕を焼き込む' },
  burnHint: {
    zh: '選一個語言，直接產出自帶字幕的 mp4。原始成片保留，不會被覆蓋。',
    en: 'Pick a language and get an mp4 with subtitles baked in. The original stays untouched.',
    ja: '言語を選ぶと字幕入り mp4 が出ます。元の映像はそのまま残ります。',
  },
  burning: { zh: '燒錄中（會重新編碼、比較慢）…', en: 'Burning (re-encoding, slow)…', ja: '焼き込み中（再エンコード）…' },
  burnDone: { zh: '燒好了', en: 'Ready', ja: '完了' },
  staleOutput: {
    zh: '接片之後有鏡頭重拍過 —— 這支成片是舊的，重按一次「接片」就會更新（$0）。',
    en: 'A shot was retaken after assembly — this cut is stale. Re-assemble to refresh ($0).',
    ja: '結合後にカットが撮り直されています。もう一度「結合」を押すと最新になります（$0）。',
  },
}

function fill(template: string, vars: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? ''))
}

export function ProductionPanel({
  copy,
  lang,
  projectId,
  order,
  onError,
  onOpenCasting,
}: Props) {
  const [test, setTest] = useState<TestShotState | null>(null)
  const [batch, setBatch] = useState<BatchState | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [assembled, setAssembled] = useState<{ seconds: number; warning: string } | null>(
    null,
  )
  /** 一鍵模式的即時進度。null 代表沒在跑。 */
  const [autoProgress, setAutoProgress] = useState<{ n: number; total: number } | null>(
    null,
  )
  const [autoStopped, setAutoStopped] = useState(false)
  /**
   * 中止旗標用 ref 不用 state。
   * 一鍵迴圈是一個長時間執行的 async function，它拿到的是**啟動當下**
   *    那次 render 的 state 快照 —— 中途 setState 它讀不到。
   *    ref 是同一個物件參照，迴圈每一圈讀到的都是最新值。
   */
  const cancelAuto = useRef(false)
  /** 燒好的字幕影片，依語言存。 */
  const [burned, setBurned] = useState<Record<string, { url: string; name: string }>>({})

  const load = useCallback(async () => {
    try {
      const [t, b] = await Promise.all([
        videoApi.testShot(projectId, order),
        videoApi.batch(projectId, order),
      ])
      setTest(t)
      setBatch(b)
      // 成品狀態從後端還原：
      //    assembled / burned 原本只活在前端 state，F5 之後
      //    下載鍵、字幕鍵、已燒好的檔案全部消失 —— 東西明明都在雲端。
      if (b.output) {
        setAssembled({ seconds: b.output.seconds, warning: '' })
        setBurned(
          Object.fromEntries(
            Object.entries(b.output.burned).map(([code, url]) => [
              code,
              { url, name: url.split('/').slice(-2)[0] },
            ]),
          ),
        )
      } else {
        // 重置過的段落，成品宣稱要跟著消失 —— 否則畫面還掛著舊下載鍵
        setAssembled(null)
        setBurned({})
      }
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    }
  }, [projectId, order, onError])

  useEffect(() => {
    void load()
  }, [load])

  /**
   * 一鍵：連續生成剩下的所有鏡頭。
   *
   * 為什麼是前端迴圈而不是後端一個 endpoint 跑完：
   *   Cloud Run 請求逾時 300 秒，一鏡 1–3 分鐘，十二鏡必定超時。
   *   一個 request 一鏡，才能顯示「第 3/12 鏡」、才能中途停、
   *   而且關掉瀏覽器不會留下半段狀態。
   *
   * 出錯就停，不繼續。錯誤通常是配額或內容政策 ——
   *    繼續跑只會把剩下十鏡的錢一起燒掉、然後得到十個一樣的錯誤。
   */
  const runAuto = async () => {
    if (busy || !batch) return
    cancelAuto.current = false
    setAutoStopped(false)
    onError('')
    setBusy('auto')

    const total = batch.total
    let now = batch
    // 生成前關卡擋下的鏡先跳過、其他照生，最後再列出來（範例 3：一鍵生成卡在第 11 鏡，
    //    使用者睡一覺起來後面 12 鏡一鏡都沒動）。擋下的第一鏡的錯誤留在畫面上，「讓 Nemotron 修這一鏡」才會出現。
    const skipped: { shot: string; msg: string }[] = []

    try {
      for (;;) {
        if (cancelAuto.current) {
          setAutoStopped(true)
          break
        }
        const todo = now.clips.filter((c) => c.status === 'not_started' && !skipped.some((s) => s.shot === c.shot_id))
        if (!todo.length) break
        const done = now.clips.filter((c) => c.status !== 'not_started').length
        setAutoProgress({ n: done + skipped.length + 1, total })
        try {
          now = await videoApi.nextClip(projectId, order, true, todo[0].shot_id)
          setBatch(now)
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e)
          if (!PREFLIGHT_RE.test(msg)) throw e
          skipped.push({ shot: todo[0].shot_id, msg })
        }
      }
      if (skipped.length) {
        onError(skipped[0].msg + (skipped.length > 1
          ? `\n（另外也沒過生成前關卡、先跳過：${skipped.slice(1).map((s) => s.shot).join('、')}）` : ''))
      }
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    } finally {
      setAutoProgress(null)
      setBusy(null)
      cancelAuto.current = false
      await load()
    }
  }

  if (!test || !batch) return null

  /** 包住所有會花錢的動作：統一擋重複點擊、統一回報錯誤、統一重新載入。 */
  const run = async (key: string, fn: () => Promise<unknown>) => {
    if (busy) return
    setBusy(key)
    onError('')
    try {
      await fn()
      await load()
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const done = batch.approved === batch.total && batch.total > 0
  const remaining = batch.total - (batch.approved + batch.awaiting)
  // 第 5 關的閘門。後端也會擋（回 409），但那是**按了才知道** ——
  //    按鈕看起來能按卻按不動，是最糟的一種介面。
  //    實際走 UI 時就是這樣撞上的：還沒選角就能按試拍。
  const castingReady = test.casting_ready !== false

  return (
    <>
      {/* ── 第 5 關沒過：擋在最前面，並且給一個去得了的入口 ───── */}
      {!castingReady && (
        <section className="section casting-gate">
          <div className="section-head">
            <h2>{t(EXTRA.castingGateTitle, lang)}</h2>
            <button className="btn primary small" onClick={onOpenCasting}>
              {t(EXTRA.castingGateGo, lang)}
            </button>
          </div>
          <p className="prompts-note">{t(EXTRA.castingGateWhy, lang)}</p>
          {test.pending_characters?.length > 0 && (
            <div className="prompts-lang">
              <span className="prompts-download-label">
                {t(EXTRA.castingPending, lang)}
              </span>
              {test.pending_characters.map((name) => (
                <span className="badge not-started" key={name}>
                  {name}
                </span>
              ))}
            </div>
          )}
        </section>
      )}

      {/* ── 第 7 關：試拍 ─────────────────────────────────── */}
      <section className="section">
        <div className="section-head">
          <h2>
          <Icon name="testshot" size={80} className="section-icon" />
          {copy.testShotTitle}
        </h2>
          {test.approved && <span className="tag good">{copy.testShotApproved}</span>}
        </div>
        <p className="prompts-note">{copy.testShotWhy}</p>

        {test.record ? (
          <div className="prod-clip">
            <video src={test.record.video_url} controls preload="metadata" />
            <div className="prod-clip-meta">
              <span className="prompt-id">{test.record.shot_id}</span>
              <span className="prompt-len">
                {test.record.seconds}
                {copy.seconds} · {test.record.width}×{test.record.height}
              </span>
              <span className="price">${test.record.cost_usd}</span>
            </div>
            <VerifyBlock lang={lang} projectId={projectId} order={order} shotId={test.record.shot_id}
              verify={test.record.verify} diagnosis={test.record.diagnosis} onChanged={() => void load()} />
            {!test.approved && (
              <div className="prod-actions">
                <button
                  className="btn primary"
                  disabled={!!busy}
                  onClick={() =>
                    run('approve-test', () => videoApi.approveTestShot(projectId, order))
                  }
                >
                  {copy.testShotApprove}
                </button>
                <button
                  className="btn"
                  disabled={!!busy || !castingReady}
                  onClick={() => run('test', () => videoApi.runTestShot(projectId, order))}
                >
                  {busy === 'test' ? copy.testShotRunning : copy.testShotRetake}
                </button>
              </div>
            )}
          </div>
        ) : (
          <div className="prod-actions">
            <button
              className="btn primary"
              disabled={!!busy || !castingReady}
              onClick={() => run('test', () => videoApi.runTestShot(projectId, order))}
            >
              {busy === 'test' ? copy.testShotRunning : copy.testShotRun}
              <span className="price">${test.estimated_cost_usd}</span>
            </button>
            <span className="prompt-preview">
              {test.candidate_shot_id} · {test.candidate_description}
            </span>
          </div>
        )}
      </section>

      {/* ── 第 8 關：批次 ─────────────────────────────────── */}
      <section className="section">
        <div className="section-head">
          <h2>
          <Icon name="generating" size={80} className="section-icon" />
          {copy.batchTitle}
        </h2>
          <span className="section-note">
            {batch.approved} / {batch.total} {copy.batchProgress}
          </span>
          <span className="price">
            ${batch.spent_usd.toFixed(2)}
            {batch.remaining_estimate_usd > 0 && (
              <> · {copy.batchRemaining} ${batch.remaining_estimate_usd.toFixed(2)}</>
            )}
          </span>
        </div>
        <p className="prompts-note">{copy.batchWhy}</p>

        {/* 試拍沒過就講清楚為什麼不能開始，不要只給一個灰掉的按鈕 */}
        {!batch.can_start && <div className="notice warn">{sysText(batch.blocked_reason, lang)}</div>}

        {batch.can_start && batch.next_shot && (
          <div className="prod-actions">
            <button
              className="btn primary"
              disabled={!!busy || batch.awaiting > 0}
              onClick={() => run('next', () => videoApi.nextClip(projectId, order))}
            >
              {busy === 'next' ? copy.batchGenerating : copy.batchNext}
              <span className="price">${batch.next_shot.cost_usd}</span>
            </button>
            <span className="prompt-preview">
              {batch.next_shot.shot_id} · <AiText text={batch.next_shot.description} lang={lang} projectId={projectId} />
            </span>
          </div>
        )}

        {/* 一鍵模式 —— 給不想逐鏡確認的使用者。
            跟逐鏡按鈕並存，不是取代。剩 0 鏡就不顯示。 */}
        {batch.can_start && remaining > 0 && (
          <div className="auto-run">
            {busy === 'auto' ? (
              <>
                <div className="auto-bar">
                  <div
                    className="auto-bar-fill"
                    style={{
                      width: `${((autoProgress?.n ?? 1) / (autoProgress?.total || 1)) * 100}%`,
                    }}
                  />
                </div>
                <div className="auto-status">
                  <span className="auto-spin" aria-hidden="true" />
                  <span>
                    {t(EXTRA.autoRunning, lang)} ·{' '}
                    {autoProgress
                      ? fill(t(EXTRA.autoShot, lang), {
                          n: autoProgress.n,
                          total: autoProgress.total,
                        })
                      : ''}
                  </span>
                  <button
                    className="btn ghost small"
                    onClick={() => {
                      cancelAuto.current = true
                    }}
                  >
                    {t(EXTRA.autoStop, lang)}
                  </button>
                </div>
              </>
            ) : (
              <div className="prod-actions">
                <button className="btn" disabled={!!busy} onClick={() => void runAuto()}>
                  {t(EXTRA.autoRun, lang)}
                  <span className="price">
                    ~${(remaining * (batch.next_shot?.cost_usd ?? 0)).toFixed(2)}
                  </span>
                </button>
                <span className="prompt-preview">{t(EXTRA.autoHint, lang)}</span>
              </div>
            )}
          </div>
        )}

        {autoStopped && <div className="notice">{t(EXTRA.autoStopped, lang)}</div>}

        {/* 全部生完要**指路**，不能只說「完成了」：
            結束之後使用者不知道下一步是什麼，等於還是卡住。
            接片區塊就在同一頁下面，但沒有人告訴使用者那是下一步 ——
            一個沒有出口的完成畫面，體感跟卡住一模一樣。 */}
        {done && (
          <div className="notice">
            <strong>{copy.batchDone}</strong>
            <div className="actions" style={{ marginTop: 12 }}>
              <button
                className="btn primary small"
                onClick={() =>
                  document
                    .getElementById('assemble-section')
                    ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                }
              >
                {copy.batchDoneNext}
              </button>
              <span className="prompts-lang-note">{copy.batchDoneHint}</span>
            </div>
          </div>
        )}

        <div className="prod-clips">
          {batch.clips
            .filter((c) => c.status !== 'not_started')
            .map((clip) => (
              <ClipCard
                key={clip.shot_id}
                copy={copy}
                lang={lang}
                clip={clip}
                busy={busy}
                verifyBlock={<VerifyBlock lang={lang} projectId={projectId} order={order} shotId={clip.shot_id}
                  verify={clip.verify} diagnosis={clip.diagnosis} onChanged={() => void load()} />}
                onApprove={() =>
                  run(`ok-${clip.shot_id}`, () =>
                    videoApi.approveClip(projectId, order, clip.shot_id),
                  )
                }
                onRetry={() =>
                  run(`re-${clip.shot_id}`, () =>
                    videoApi.retryClip(projectId, order, clip.shot_id),
                  )
                }
                onRetryMute={() =>
                  run(`re-${clip.shot_id}`, () =>
                    videoApi.retryClip(projectId, order, clip.shot_id, true),
                  )
                }
                onChooseTake={(attempt) =>
                  run(`tk-${clip.shot_id}`, () =>
                    videoApi.chooseTake(projectId, order, clip.shot_id, attempt),
                  )
                }
              />
            ))}
        </div>

        {batch.approved > 0 && (
          <div className="prod-reset">
            <button
              className="btn ghost small"
              disabled={!!busy}
              onClick={() => run('reset', () => videoApi.resetBatch(projectId, order))}
            >
              {copy.batchReset}
            </button>
            <span className="prompts-lang-note">{copy.batchResetHint}</span>
          </div>
        )}
      </section>

      {/* ── 第 9 關：接片與字幕 ───────────────────────────── */}
      {batch.approved > 0 && (
        <section className="section" id="assemble-section">
          <div className="section-head">
            <h2>
          <Icon name="subtitles" size={80} className="section-icon" />
          {copy.assembleTitle}
        </h2>
            <span className="price">$0</span>
          </div>
          <p className="prompts-note">{copy.assembleWhy}</p>

          <div className="prod-actions">
            <button
              className="btn primary"
              disabled={!!busy}
              onClick={() =>
                run('assemble', async () => {
                  const r = await videoApi.assemble(projectId, order)
                  setAssembled({ seconds: r.seconds, warning: r.warning })
                })
              }
            >
              {busy === 'assemble' ? copy.assembling : copy.assembleRun}
            </button>

            {assembled && (
              <a className="btn" href={videoApi.downloadUrl(projectId, order)} download>
                {copy.assembleDownload}
                <span className="price">{assembled.seconds}s</span>
              </a>
            )}
          </div>

          {assembled?.warning && <div className="notice warn">{assembled.warning}</div>}

          {/* 反悔重拍之後成片會過期 —— 不講的話，下載到的是舊片而不自知 */}
          {batch.output?.stale && (
            <div className="notice warn">{t(EXTRA.staleOutput, lang)}</div>
          )}

          {assembled && (
            <>
              <div className="prompts-lang">
                <span className="prompts-download-label">{copy.assembleSubtitles}</span>
                {LANGS.map((l) => (
                  <a
                    key={l.code}
                    className="btn small"
                    href={videoApi.subtitleUrl(projectId, order, l.code as Lang)}
                    download
                  >
                    {l.label}
                  </a>
                ))}
              </div>
              <p className="prompts-lang-note">{copy.assembleSubtitleHint}</p>

              {/* 燒進影片 —— 合成字幕可選中文、英文、日文。
                  上面那排是「下載 .srt 自己去合成」，這排是「直接給你成品」。
                  兩個都留：有人要 srt 拿去別的軟體調樣式。 */}
              <div className="burn-block">
                <div className="prompts-lang">
                  <span className="prompts-download-label">
                    {t(EXTRA.burnTitle, lang)}
                  </span>
                  {LANGS.map((l) => {
                    const code = l.code as Lang
                    const got = burned[code]
                    const isBurning = busy === `burn-${code}`
                    return got ? (
                      <a key={l.code} className="btn small primary" href={got.url} download>
                        {l.label} · {t(EXTRA.burnDone, lang)}
                      </a>
                    ) : (
                      <button
                        key={l.code}
                        className="btn small"
                        disabled={!!busy}
                        onClick={() =>
                          run(`burn-${code}`, async () => {
                            const r = await videoApi.burnSubtitles(projectId, order, code)
                            setBurned((prev) => ({
                              ...prev,
                              [code]: { url: r.download_url, name: r.filename },
                            }))
                          })
                        }
                      >
                        {isBurning ? '…' : l.label}
                      </button>
                    )
                  })}
                </div>
                <p className="prompts-lang-note">
                  {busy?.startsWith('burn-')
                    ? t(EXTRA.burning, lang)
                    : t(EXTRA.burnHint, lang)}
                </p>
              </div>
            </>
          )}
        </section>
      )}
    </>
  )
}

/**
 * 把生成失敗的原始訊息換成人話。
 *
 * 為什麼要有這個：
 *    被內容審查擋下時，卡片上直接印的是 Google 回的原始 JSON ——
 *    `HTTP 400 Omni 送出失敗：{"error":{"message":"The input could not…`。
 *    後端**有**把它翻成人話，但那是生成當下才做的事，
 *    **已經存在專案裡的舊失敗紀錄仍然是原文**，而使用者看到的就是那些。
 *
 * 只換已知的那一種。認不得的錯誤原樣顯示 ——
 *    看不懂的英文訊息仍然比一句「發生錯誤」有用，
 *    至少能貼給別人問。
 */
function humanError(err: string, copy: Copy): string {
  if (!err) return '—'
  if (err.includes('Responsible AI') || err.includes('violates')) {
    return copy.errorBlocked
  }
  return err
}

function ClipCard({
  copy,
  lang,
  clip,
  busy,
  onApprove,
  onRetry,
  onRetryMute,
  onChooseTake,
  verifyBlock,
}: {
  copy: Copy
  lang: Lang
  clip: ClipRecord
  busy: string | null
  verifyBlock?: React.ReactNode
  onApprove: () => void
  onRetry: () => void
  onRetryMute: () => void
  onChooseTake: (attempt: number) => void
}) {
  const waiting = clip.status === 'awaiting_approval'
  const failed = clip.status === 'failed'
  // 有兩個以上 take 才顯示選擇列 —— 只拍過一次沒得選，列出來是噪音
  const takes = clip.takes ?? []
  const showTakes = takes.length > 1

  return (
    <div className={`prod-clip${waiting ? ' waiting' : ''}${failed ? ' failed' : ''}`}>
      {clip.video_url ? (
        <video src={clip.video_url} controls preload="metadata" />
      ) : (
        <div className="casting-noimage">{humanError(clip.error, copy)}</div>
      )}

      <div className="prod-clip-meta">
        <span className="prompt-id">{clip.shot_id}</span>
        <span className="prompt-len">
          {clip.seconds}
          {copy.seconds}
        </span>
        {/* 重拍過的要看得出來 —— cost 是累計值，attempts 解釋了為什麼比別鏡貴 */}
        {clip.attempts > 1 && (
          <span className="tag guess">
            {copy.batchAttempts.replace('{n}', String(clip.attempts))}
          </span>
        )}
        <span className="price">${clip.cost_usd}</span>
        {clip.status === 'approved' && <span className="tag good">✓</span>}
      </div>

      {/* 歷次 take 選擇列：每一次生成都是使用者花錢買的資產，
          重拍不保證更好 —— 用哪一次由導演決定，切換 $0、隨時切回。 */}
      {showTakes && (
        <div className="prod-takes">
          <span className="prompts-lang-note">{copy.takesLabel}</span>
          {takes.map((t) => {
            const current = t.uri === clip.uri
            return (
              <button
                key={t.attempt}
                className={`btn small${current ? ' primary' : ' ghost'}`}
                disabled={!!busy || current}
                onClick={() => onChooseTake(t.attempt)}
                title={`${t.seconds}${copy.seconds} · ${t.generated_at.slice(0, 16)}`}
              >
                #{t.attempt}
                {current ? ` ✓ ${copy.takesCurrent}` : ''}
              </button>
            )
          })}
          <span className="prompts-lang-note">{copy.takesSwitched}</span>
        </div>
      )}

      {clip.warnings.filter((w) => !(clip.verify && w.includes('查證：'))).map((w, i) => (
        <div className="notice warn" key={i}>
          {sysText(w, lang)}
        </div>
      ))}

      {/* 本機查證＋診斷（有查證結果時，查證的警告收在這一塊裡、不重複列在上面） */}
      {verifyBlock}

      {/* 被擋時要指出兇手：失敗時要告訴使用者是哪一句台詞、
          什麼內容過不了。API 只回一句籠統的
          violates Responsible AI practices —— 在 32 鏡的分鏡表裡等於沒說。
          措辭刻意保留餘地（「最可能」）：這是讀出來的推測不是判決。 */}
      {failed && clip.rejection?.explanation && (
        <div className="notice warn">
          <strong>{copy.rejectWhy}</strong>
          <p>{clip.rejection.explanation}</p>
          {clip.rejection.excerpt && (
            <blockquote className="screening-excerpt">
              {clip.rejection.excerpt}
            </blockquote>
          )}
          {clip.rejection.suggestion && (
            <>
              <strong>{copy.rejectSuggestion}</strong>
              <p>{clip.rejection.suggestion}</p>
            </>
          )}
        </div>
      )}

      {/* 被內容安全審查擋下的鏡頭（台詞踩敏感詞整鏡被拒）：
          重試同樣內容必然再被擋，所以主按鈕是「無聲重拍」——
          畫面照生、台詞改由字幕呈現，劇本一個字不動。 */}
      {failed && (
        <div className="prod-actions">
          {clip.error.includes('安全審查') ||
          clip.error.includes('Responsible AI') ? (
            <>
              <button
                className="btn primary small"
                disabled={!!busy}
                onClick={onRetryMute}
              >
                {busy === `re-${clip.shot_id}`
                  ? copy.batchGenerating
                  : copy.batchRetryMute}
              </button>
              <span className="prompts-lang-note">{copy.batchRetryMuteHint}</span>
            </>
          ) : (
            <button className="btn primary small" disabled={!!busy} onClick={onRetry}>
              {busy === `re-${clip.shot_id}` ? copy.batchGenerating : copy.batchRetry}
            </button>
          )}
        </div>
      )}

      {waiting && (
        <div className="prod-actions">
          <button className="btn primary small" disabled={!!busy} onClick={onApprove}>
            {copy.batchApprove}
          </button>
          <button className="btn small" disabled={!!busy} onClick={onRetry}>
            {busy === `re-${clip.shot_id}` ? copy.batchGenerating : copy.batchRetry}
          </button>
          <span className="prompts-lang-note">{copy.batchWaiting}</span>
        </div>
      )}

      {/* 已通過也能反悔：生了 12 鏡之後回頭看，
          中間某一鏡有問題，就單獨重拍那一鏡。
          後端 retry 本來就不鎖狀態，缺的只是這顆按鈕。
          重拍後回到待確認，接好的段落成片會過期 —— 那是正常狀態，重接就好。 */}
      {clip.status === 'approved' && (
        <div className="prod-actions">
          <button className="btn ghost small" disabled={!!busy} onClick={onRetry}>
            {busy === `re-${clip.shot_id}` ? copy.batchGenerating : copy.batchRetake}
          </button>
          <span className="prompts-lang-note">{copy.batchRetakeHint}</span>
        </div>
      )}
    </div>
  )
}
