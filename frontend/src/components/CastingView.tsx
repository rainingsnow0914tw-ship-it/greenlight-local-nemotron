/**
 * 選角關卡。
 *
 * 第 5 關，硬性閘門：每個角色生三張候選圖，不滿意可以重抽（圖比影片便宜），
 * 選定之後才能進入生成。
 *
 * 這個畫面要守住的三件事：
 *   1. 零輸入。全部是按鈕 —— 重抽只能挑方向，不能打字。
 *   2. 誠實標示。劇本沒寫外貌的角色，每張候選圖都要標「系統推測」
 *      並說明根據什麼推的。使用者有權知道哪些是他自己寫的。
 *   3. 價格印在按鈕上。按下去之前就看得到這一按花多少。
 *
 * 版面選擇：角色做成橫向 chips、一次只展開一個。
 * 六個角色各自攤開候選圖會變成一條看不完的長捲軸，
 * 而選角是一次專心處理一個人的工作。
 */

import { Icon } from './Icons'
import { AiText } from './AiText'
import { sysText } from '../sysText'
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  castingApi,
  t,
  type CastingSession,
  type CastingState,
  type Lang,
} from '../api'
import type { Copy } from '../i18n'

interface Props {
  copy: Copy
  lang: Lang
  projectId: string
  onError: (msg: string) => void
  /** 選角全部完成時通知外層（讓進度條與下一關按鈕跟著更新） */
  onGateChange?: (canProceed: boolean) => void
}

export function CastingView({ copy, lang, projectId, onError, onGateChange }: Props) {
  const [state, setState] = useState<CastingState | null>(null)
  const [active, setActive] = useState<string | null>(null)
  // 每個角色各自的忙碌狀態，不是一個全域布林：
  //     若是單一 `useState(false)`，按任何一個角色生成，所有角色的按鈕
  //     全部鎖住。而一輪要 20–30 秒（撞到配額退避會更久），
  //     等於一次只能做一個角色，多個角色就得站在螢幕前輪流等。
  //     角色之間本來就互不相干，資料層不該鎖成一片。
  const [busyChars, setBusyChars] = useState<Set<string>>(new Set())
  // 舊輪次預設收合 —— 抽了五輪之後畫面上會有十五張圖，
  // 但使用者當下在看的是最新一輪。展開的入口留著，因為
  // 「還是第二輪那張好」是很常見的事。
  const [showHistory, setShowHistory] = useState(false)
  // 造型模式：null = 全片預設（一般選角），數字 = 為第 N 段生造型。
  // 服裝是段落層的東西，臉是全片鎖死的 —— 同一個面板兩種模式，
  // 不另開一頁：候選格、方向按鈕、歷史都共用，只有「選定」的語意不同。
  const [lookSegment, setLookSegment] = useState<number | null>(null)

  // callback 存進 ref，不要讓 load 依賴它們：
  //
  //     若父層傳下來的是 inline 函式（`onGateChange={() => void refresh()}`），
  //     每次 render 都是新的參照。`load` 的 useCallback 若依賴 onGateChange
  //     → load 重建 → useEffect 重跑 → 打 API → onGateChange → refresh()
  //     → App 重繪 → 又一個新函式 → 無限迴圈。
  //
  //     實測：六秒內打了 12 次 `GET /casting`，而使用者按的
  //     `POST /rounds` 一個都沒送出去 —— 畫面一直在重載，點擊被吃掉，
  //     體感是「有點卡」「按了沒送過來」。
  //
  //     呼叫端當然該用 useCallback，但元件不該因為
  //     父層傳了 inline 函式就進入無限迴圈 —— 那個地雷太容易再踩。
  //     ref 讓 load 的相依只剩 projectId，穩定。
  const onErrorRef = useRef(onError)
  const onGateChangeRef = useRef(onGateChange)
  useEffect(() => {
    onErrorRef.current = onError
    onGateChangeRef.current = onGateChange
  })

  const load = useCallback(async () => {
    try {
      const data = await castingApi.state(projectId)
      setState(data)
      setActive((prev) => prev ?? data.sessions[0]?.character_name ?? null)
      onGateChangeRef.current?.(data.gate.can_proceed)
    } catch (e) {
      onErrorRef.current(e instanceof Error ? e.message : String(e))
    }
  }, [projectId])

  useEffect(() => {
    void load()
  }, [load])

  if (!state) return <p className="empty">{copy.loading}</p>

  const session =
    state.sessions.find((s) => s.character_name === active) ?? state.sessions[0]

  // 生成類動作硬性一次一個（產品決定）：
  //     後端本來就要排隊，與其讓使用者同時按了再等更久，
  //     不如按完一個再接下一個，體感比較好。
  //
  //     有角色在生成時，其他角色的生成按鈕直接鎖住（灰掉）——
  //     不排隊、不假裝並發。選定/解鎖是零秒操作，不受此限。
  //     底層的交易式寫入照留（多分頁併發時資料仍不互蓋）。
  const generating = busyChars.size > 0

  const run = async (
    character: string,
    fn: () => Promise<{ session: CastingSession }>,
  ) => {
    if (busyChars.has(character)) return
    setBusyChars((prev) => new Set(prev).add(character))
    onError('')
    try {
      const result = await fn()
      setState((prev) =>
        prev
          ? {
              ...prev,
              sessions: prev.sessions.map((s) =>
                s.character_name === result.session.character_name
                  ? result.session
                  : s,
              ),
            }
          : prev,
      )
      // 閘門狀態整份重讀 —— 它是所有角色的聚合
      await load()
    } catch (e) {
      onErrorRef.current(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyChars((prev) => {
        const next = new Set(prev)
        next.delete(character)
        return next
      })
    }
  }

  const gate = state.gate

  return (
    <section className="casting">
      <header className="casting-head">
        <h2>
          <Icon name="casting" size={80} className="section-icon" />
          {copy.castingTitle}
        </h2>
        <p className="casting-why">{copy.castingWhy}</p>

        <div className={`casting-gate ${gate.can_proceed ? 'ok' : 'blocked'}`}>
          <span className="casting-gate-count">
            {gate.characters_locked} / {gate.characters_total} {copy.castingProgress}
          </span>
          <span className="casting-gate-msg">
            {gate.can_proceed ? copy.castingReady : sysText(gate.blocked_reason, lang)}
          </span>
          <span className="casting-gate-spent">
            ${gate.total_spent_usd.toFixed(2)}
          </span>
        </div>
      </header>

      <div className="casting-chips">
        {state.sessions.map((s) => (
          <button
            key={s.character_name}
            className={`casting-chip${s.character_name === session?.character_name ? ' on' : ''}${
              s.is_locked ? ' locked' : ''
            }`}
            onClick={() => {
              setActive(s.character_name)
              setShowHistory(false)
              setLookSegment(null)
            }}
          >
            <span className="casting-chip-name">{s.character_name}</span>
            <span className="casting-chip-meta">
              {/* 正在生成的角色要在 chip 上看得到 —— 現在可以同時跑
                  好幾個，切走之後沒有指示的話，使用者不知道背景還有事在跑。 */}
              {busyChars.has(s.character_name) ? (
                <span className="chip-spin" aria-label="生成中" />
              ) : s.is_locked ? (
                '✓'
              ) : s.rounds_used > 0 ? (
                `${s.rounds_used}`
              ) : (
                '—'
              )}
            </span>
          </button>
        ))}
      </div>

      {session && (
        <CharacterPanel
          copy={copy}
          lang={lang}
          projectId={projectId}
          session={session}
          aspectRatio={state.aspect_ratio || '16:9'}
          roundCost={state.round_cost_usd}
          perRound={state.candidates_per_round}
          busy={busyChars.has(session.character_name)}
          generateBusy={generating}
          showHistory={showHistory}
          onToggleHistory={() => setShowHistory((v) => !v)}
          segments={state.segments ?? []}
          lookSegment={lookSegment}
          onLookSegment={setLookSegment}
          onGenerate={(direction) =>
            run(session.character_name, () =>
              castingApi.generateRound(
                projectId, session.character_name, direction, lookSegment ?? undefined,
              ),
            )
          }
          onSelect={(candidateId) =>
            run(session.character_name, () =>
              castingApi.select(
                projectId, session.character_name, candidateId, lookSegment ?? undefined,
              ),
            )
          }
          onUnlock={() =>
            run(session.character_name, () =>
              castingApi.unlock(projectId, session.character_name, lookSegment ?? undefined),
            )
          }
          onUploadIdentity={(file, consent) =>
            run(session.character_name, () =>
              castingApi.uploadIdentity(projectId, session.character_name, file, consent),
            )
          }
          onClearIdentity={() =>
            run(session.character_name, () =>
              castingApi.clearIdentity(projectId, session.character_name),
            )
          }
          onGenerateFromIdentity={() => {
            // 上傳照片之後要能直接生角色卡，不能沒有生成按鈕：
            //    上傳時角色多半已鎖定，而鎖定狀態沒有生成按鈕。上傳新臉的意思就是要換人：
            //    先解鎖（uploaded 的身份參考在 unlock 時保留）、再用這張臉生一輪 initial。
            setLookSegment(null)
            run(session.character_name, async () => {
              if (session.is_locked) {
                await castingApi.unlock(projectId, session.character_name)
              }
              return castingApi.generateRound(projectId, session.character_name, 'initial')
            })
          }}
        />
      )}
    </section>
  )
}

interface PanelProps {
  copy: Copy
  lang: Lang
  projectId: string
  session: CastingSession
  /** 專案影片比例 —— 卡片框跟著它（圖已按專案比例生成） */
  aspectRatio: string
  roundCost: number
  perRound: number
  busy: boolean
  /** 任何角色正在生成 —— 生成類按鈕全域鎖（一次只做一個） */
  generateBusy: boolean
  showHistory: boolean
  onToggleHistory: () => void
  onGenerate: (direction: string) => void
  onSelect: (candidateId: string) => void
  onUnlock: () => void
  /** 段落清單（造型模式下拉） */
  segments: { order: number; title: string }[]
  /** null = 全片預設；數字 = 為第 N 段生造型 */
  lookSegment: number | null
  onLookSegment: (order: number | null) => void
  onUploadIdentity: (file: File, consent: boolean) => void
  onClearIdentity: () => void
  /** 用身份參考（臉）重生角色卡：已鎖定就先解鎖再生（上傳新臉＝要換人） */
  onGenerateFromIdentity: () => void
}

function CharacterPanel({
  copy,
  lang,
  projectId,
  session,
  aspectRatio,
  roundCost,
  perRound,
  busy,
  generateBusy,
  showHistory,
  onToggleHistory,
  onGenerate,
  onSelect,
  onUnlock,
  segments,
  lookSegment,
  onLookSegment,
  onUploadIdentity,
  onClearIdentity,
  onGenerateFromIdentity,
}: PanelProps) {
  const lookMode = lookSegment != null
  // 一般模式只看一般輪（segment_order == null）—— 造型輪混進來會讓人以為
  // 「這張也可以拿來鎖角色」。
  // 造型模式：顯示全片預設那張 + 所有段落曾生過的造型 —— 使用者可能不想
  // 生成新的，而是要沿用前面章節選過的。每張標明目前用在哪幾段，
  // 直接按「用在第 N 段」沿用，不必重生。
  const pool = session.candidates.filter((c) =>
    lookMode
      ? c.segment_order != null || c.candidate_id === session.selected_candidate_id
      : c.segment_order == null,
  )
  const latestRound = pool.length ? Math.max(...pool.map((c) => c.round_number)) : 0
  // 造型模式一律全部展開（本來就是要讓他看到別段的），一般模式維持只看最新輪
  const visible = pool.filter((c) => lookMode || showHistory || c.round_number === latestRound)
  const hasHistory = !lookMode && new Set(pool.map((c) => c.round_number)).size > 1
  const lookInUseId = lookMode ? session.segment_looks[String(lookSegment)] : undefined
  // candidate_id → 用在哪些段（給造型模式的標籤）
  const usedIn: Record<string, number[]> = {}
  for (const [seg, cid] of Object.entries(session.segment_looks)) {
    ;(usedIn[cid] ??= []).push(Number(seg))
  }
  const fmt = (s: string, n: number | null) => s.replace('{n}', String(n ?? ''))

  // 上傳身份參考：勾了免責才收檔（後端也擋）。file input 是「零輸入」原則下唯一允許的輸入。
  const [consent, setConsent] = useState(false)
  const [showUpload, setShowUpload] = useState(false)
  const fileRef = useRef<HTMLInputElement | null>(null)

  return (
    <div className="casting-panel">
      <div className="casting-person">
        <h3>{session.character_name}</h3>
        <span className="tag">
          {session.is_principal ? copy.principal : copy.supporting}
        </span>
        {session.kind === 'non_human' && <span className="tag alt">{copy.nonHuman}</span>}
        <span className={`tag ${session.has_explicit_description ? 'good' : 'guess'}`}>
          {session.has_explicit_description ? copy.castingFromScript : copy.castingInferred}
        </span>
      </div>

      {session.source_description && (
        <p className="casting-source">{session.source_description}</p>
      )}

      {/* ── 身份參考（臉）—— 臉是輸入、造型是產出 ── */}
      <div className="identity-box">
        <div className="identity-head">
          <strong>{copy.identityTitle}</strong>
          {session.identity_source && (
            <span className="tag">
              {session.identity_source === 'uploaded' ? copy.identityUploaded : copy.identityGenerated}
            </span>
          )}
        </div>
        <div className="identity-body">
          {session.identity_reference_url ? (
            <img className="identity-thumb" src={session.identity_reference_url} alt="" />
          ) : (
            <p className="identity-none">{copy.identityNone}</p>
          )}
          <div className="identity-actions">
            <button
              className="btn ghost small"
              onClick={() => setShowUpload((v) => !v)}
              disabled={busy}
            >
              {session.identity_reference_url ? copy.identityReplace : copy.identityUpload}
            </button>
            {session.identity_reference_url && (
              <button className="btn ghost small" onClick={onClearIdentity} disabled={busy}>
                {copy.identityClear}
              </button>
            )}
          </div>
        </div>
        {session.identity_source === 'uploaded' && (
          <div className="identity-generate">
            <button
              className="btn primary"
              onClick={onGenerateFromIdentity}
              disabled={generateBusy}
            >
              {busy ? copy.castingGenerating : copy.identityGenerate}
              <span className="price">${roundCost.toFixed(2)} · {perRound}</span>
            </button>
            {session.is_locked && (
              <p className="identity-hint">{copy.identityGenerateUnlockNote}</p>
            )}
          </div>
        )}
        {showUpload && (
          <div className="identity-upload">
            <p className="identity-hint">{copy.identityHint}</p>
            <label className="identity-consent">
              <input
                type="checkbox"
                checked={consent}
                onChange={(e) => setConsent(e.target.checked)}
              />
              <span>{copy.identityConsent}</span>
            </label>
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              disabled={!consent || busy}
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (!f) return
                if (!consent) return
                onUploadIdentity(f, true)
                setShowUpload(false)
                if (fileRef.current) fileRef.current.value = ''
              }}
            />
            {!consent && <p className="identity-required">{copy.identityConsentRequired}</p>}
            {busy && <p className="casting-waiting">{copy.identityUploading}</p>}
          </div>
        )}
      </div>

      {/* ── 造型用於：全片預設 / 第 N 段（服裝是段落層） ── */}
      {segments.length > 0 && (
        <div className="look-mode">
          <span className="look-mode-title">{copy.lookModeTitle}</span>
          <div className="look-mode-chips">
            <button
              className={`btn chip${!lookMode ? ' on' : ''}`}
              onClick={() => onLookSegment(null)}
            >
              {copy.lookModeDefault}
            </button>
            {segments.map((seg) => {
              const has = Boolean(session.segment_looks[String(seg.order)])
              return (
                <button
                  key={seg.order}
                  className={`btn chip${lookSegment === seg.order ? ' on' : ''}${has ? ' has-look' : ''}`}
                  onClick={() => onLookSegment(seg.order)}
                  title={seg.title}
                >
                  {fmt(copy.lookModeSegment, seg.order)}
                  {has ? ' ✓' : ''}
                </button>
              )
            })}
          </div>
          {lookMode && <p className="look-mode-hint">{copy.lookModeHint}</p>}
        </div>
      )}

      {lookMode ? (
        <div className="casting-actions">
          {!session.is_locked ? (
            <p className="casting-notice warn">{copy.lookNeedLock}</p>
          ) : (
            <>
              <p className="casting-reroll-title">
                {fmt(copy.lookGenerate, lookSegment)}
                <span className="price">${roundCost.toFixed(2)}</span>
              </p>
              <div className="casting-directions">
                {session.directions
                  .filter((d) => d.look_variant)
                  .map((d) => (
                    <button
                      key={d.direction_id}
                      className="btn direction"
                      onClick={() => onGenerate(d.direction_id)}
                      disabled={generateBusy}
                      title={t(d.hint, lang)}
                    >
                      <span className="direction-label">{t(d.label, lang)}</span>
                      <span className="direction-hint">{t(d.hint, lang)}</span>
                    </button>
                  ))}
              </div>
              {lookInUseId && (
                <button className="btn ghost small" onClick={onUnlock} disabled={busy}>
                  {fmt(copy.lookReset, lookSegment)}
                </button>
              )}
            </>
          )}
          {busy && <p className="casting-waiting">{copy.castingGenerating}</p>}
        </div>
      ) : session.is_locked ? (
        <div className="casting-locked">
          <span>{copy.castingLockedNote}</span>
          <button className="btn ghost" onClick={onUnlock} disabled={busy}>
            {copy.castingUnlock}
          </button>
        </div>
      ) : (
        <div className="casting-actions">
          {session.rounds_used === 0 ? (
            <button className="btn primary" onClick={() => onGenerate('initial')} disabled={generateBusy}>
              {busy ? copy.castingGenerating : copy.castingGenerate}
              <span className="price">
                ${roundCost.toFixed(2)} · {perRound}
              </span>
            </button>
          ) : (
            <>
              <p className="casting-reroll-title">
                {copy.castingRerollTitle}
                <span className="price">${roundCost.toFixed(2)}</span>
              </p>
              <div className="casting-directions">
                {session.directions.map((d) => (
                  <button
                    key={d.direction_id}
                    className="btn direction"
                    onClick={() => onGenerate(d.direction_id)}
                    disabled={generateBusy}
                    title={t(d.hint, lang)}
                  >
                    <span className="direction-label">{t(d.label, lang)}</span>
                    <span className="direction-hint">{t(d.hint, lang)}</span>
                  </button>
                ))}
              </div>
            </>
          )}
          {busy && <p className="casting-waiting">{copy.castingGenerating}</p>}
        </div>
      )}

      {session.budget_notice.level !== 'none' && (
        <p className={`casting-notice ${session.budget_notice.level}`}>
          {sysText(session.budget_notice.message, lang)}
        </p>
      )}

      {session.candidates.length === 0 ? (
        <p className="empty">{copy.castingNoCandidates}</p>
      ) : (
        <>
          {hasHistory && (
            <button className="btn ghost small" onClick={onToggleHistory}>
              {showHistory
                ? `▲ ${copy.castingHideOld}`
                : `▼ ${copy.castingShowAll.replace('{n}', String(session.candidates.length))}`}
            </button>
          )}

          {aspectRatio === '16:9' && (
            <p className="prompts-lang-note">{copy.castingCropNote}</p>
          )}
          <div className="casting-grid">
            {visible.map((cand) => {
              const chosen = cand.candidate_id === session.selected_candidate_id
              return (
                <figure
                  key={cand.candidate_id}
                  className={`casting-card${chosen ? ' chosen' : ''}`}
                >
                  {cand.image_url ? (
                    // 卡片刻意維持直式裁切預覽（產品決定：直式的角色卡看起來比較舒服）。
                    //    圖的實際檔案是照專案比例生的（16:9 專案就是橫圖），
                    //    直式只是預覽裁切。下方有說明行，免得被誤會成生錯比例。
                    <img src={cand.image_url} alt={cand.interpretation} loading="lazy" />
                  ) : cand.image_skipped ? (
                    // 文字版候選：沒有生圖模型，就把要餵給影片模型的外貌與聲音描述攤開給人看
                    <div className="casting-noimage casting-textonly">
                      <strong>{copy.castingTextOnly}</strong>
                      <p>{cand.appearance_tags}</p>
                      {cand.voice_tags && <p>🎙 {cand.voice_tags}</p>}
                    </div>
                  ) : (
                    <div className="casting-noimage">{copy.castingImageFailed}</div>
                  )}
                  <figcaption>
                    <p className="casting-interp"><AiText text={cand.interpretation} lang={lang} projectId={projectId} /></p>
                    <p className="casting-basis"><AiText text={cand.inference_note} lang={lang} projectId={projectId} /></p>
                    <span className="casting-roundno">
                      {copy.castingRound.replace('{n}', String(cand.round_number))}
                    </span>
                  </figcaption>
                  {lookMode ? (
                    (cand.has_image || cand.image_skipped) && cand.candidate_id !== lookInUseId ? (
                      <button
                        className="btn primary small"
                        onClick={() => onSelect(cand.candidate_id)}
                        disabled={busy}
                      >
                        {fmt(copy.lookUse, lookSegment)}
                      </button>
                    ) : null
                  ) : (
                    !session.is_locked && (cand.has_image || cand.image_skipped) && (
                      <button
                        className="btn primary small"
                        onClick={() => onSelect(cand.candidate_id)}
                        disabled={busy}
                      >
                        {copy.castingSelect}
                      </button>
                    )
                  )}
                  {lookMode && cand.candidate_id === lookInUseId && (
                    <span className="casting-chosen-flag">{fmt(copy.lookInUse, lookSegment)}</span>
                  )}
                  {lookMode && (
                    <span className="look-used-in">
                      {cand.candidate_id === session.selected_candidate_id
                        ? copy.lookModeDefault
                        : ''}
                      {(usedIn[cand.candidate_id] ?? [])
                        .filter((n) => n !== lookSegment)
                        .map((n) => ' · ' + fmt(copy.lookInUse, n))
                        .join('')}
                    </span>
                  )}
                  {!lookMode && chosen && <span className="casting-chosen-flag">{copy.castingSelected}</span>}
                </figure>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}
