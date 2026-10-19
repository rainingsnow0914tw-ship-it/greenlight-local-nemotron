/**
 * 段落畫面 —— 定調 → 分鏡 → 看鏡頭表與方案。
 *
 * 這一頁是「零輸入」（鐵律 7）最集中的地方：
 * 使用者只按按鈕，不打任何字。風格五選一、比例二選一、
 * 然後一個標了價錢的「開始分鏡」。
 *
 * 花錢的動作一律先報價。分鏡按鈕上直接寫預估區間，
 *    按下去之前就知道會扣多少。
 */

import { Icon } from './Icons'
import { nameText, sysText } from '../sysText'
import { useCallback, useEffect, useState } from 'react'
import {
  api,
  segmentApi,
  send,
  t,
  type AspectRatioOption,
  type Lang,
  type PlanEstimate,
  type SegmentShots,
  type StylePreset,
  type Localized,
} from '../api'
import { DirectionPanel } from './DirectionPanel'
import { ScenePanel } from './ScenePanel'
import { LinesPanel } from './LinesPanel'
import { FinishPanel } from './FinishPanel'
import { AiText } from './AiText'
import { ProductionPanel } from './ProductionPanel'
import { PromptsPanel } from './PromptsPanel'
import type { Copy } from '../i18n'

interface Props {
  copy: Copy
  lang: Lang
  projectId: string
  order: number
  onError: (msg: string) => void
  /** 選角關卡擋住生成時，要能從段落頁直接跳過去。 */
  onOpenCasting: () => void
}

// Lite 已下架；Omni Flash 是主力檔
const TIER_ORDER = ['omni_flash_720p', 'fast_720p', 'standard_1080p']

const FF = {
  zh: { title: '首幀（可以用自己的圖）', note: '上傳一張自己做好的圖，這一鏡就從這張畫面開始（原圖照用，優先於接上一鏡）。', upload: '上傳首幀', clear: '不用了' },
  en: { title: 'First frame (your own picture)', note: 'Upload a picture you made and this shot starts from it, used as is (it takes priority over chaining from the previous shot).', upload: 'Upload first frame', clear: 'Remove' },
  ja: { title: '最初のフレーム（自分の画像も使える）', note: '自分で作った画像をアップロードすると、このカットはその画面から始まる（そのまま使用、前のカットからのつなぎより優先）。', upload: '最初のフレームをアップロード', clear: '使わない' },
} as const

export function SegmentView({
  copy,
  lang,
  projectId,
  order,
  onError,
  onOpenCasting,
}: Props) {
  const [styles, setStyles] = useState<StylePreset[]>([])
  const [ratios, setRatios] = useState<AspectRatioOption[]>([])
  const [style, setStyle] = useState('photoreal')
  const [ratio, setRatio] = useState('16:9')
  const [estimate, setEstimate] = useState<PlanEstimate | null>(null)
  const [shots, setShots] = useState<SegmentShots | null>(null)
  const [planning, setPlanning] = useState(false)
  const [openShot, setOpenShot] = useState<string | null>(null)
  const [formats, setFormats] = useState<
    Array<{ id: string; label: Localized; one_liner: Localized }>
  >([])
  /** null = 跟著專案走 */
  const [format, setFormat] = useState<string | null>(null)
  const [formatNote, setFormatNote] = useState('')

  const chooseFormat = async (next: string | null) => {
    setFormat(next)
    setFormatNote('')
    try {
      const r = await segmentApi.setFormat(projectId, order, next)
      // 改了不會動到已經分好的鏡頭表 —— 不講的話使用者會以為生效了，
      //    結果拍出來還是舊策略，那比不能改更糟。
      if (r.needs_replan) setFormatNote(copy.segFormatReplan)
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    }
  }

  const [ffBump, setFfBump] = useState(0)   // 換過首幀就讓 <img> 重新抓
  const load = useCallback(async () => {
    try {
      const [s, r, est, f] = await Promise.all([
        api.styles(),
        api.aspectRatios(),
        segmentApi.planEstimate(projectId, order),
        api.formats(),
      ])
      setStyles(s.presets)
      setRatios(r.options)
      setEstimate(est)
      setFormats(f.formats)
      // 段落的敘事形式覆寫從伺服器帶回來：不帶的話重新載入永遠顯示
      // 「跟專案設定」，使用者以為沒存到。null = 真的跟專案走。
      setFormat(est.segment.format_id ?? null)

      // 已經分鏡過就直接載入結果
      if (est.segment.has_shots) {
        const sh = await segmentApi.shots(projectId, order)
        setShots(sh)
        setStyle(sh.shot_list.style_id)
        setRatio(sh.shot_list.aspect_ratio)
      }
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    }
  }, [projectId, order, onError])

  useEffect(() => {
    void load()
  }, [load])

  const chooseLook = async (nextStyle: string, nextRatio: string) => {
    setStyle(nextStyle)
    setRatio(nextRatio)
    try {
      await segmentApi.setLook(projectId, {
        style_id: nextStyle,
        aspect_ratio: nextRatio,
      })
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    }
  }

  // 之前這張方案表只能看不能選 —— 後端 chosen_tier 零寫入者，
  //    永遠 fallback 最便宜檔 —— 系統建議中間檔位，使用者卻沒得選。
  const chooseTier = async (tier: string) => {
    try {
      await segmentApi.setTier(projectId, tier)
      // 重抓 shots 讓 chosen_tier 與依檔位算的建議跟著更新
      const sh = await segmentApi.shots(projectId, order)
      setShots(sh)
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    }
  }

  const startPlanning = async (force = false) => {
    setPlanning(true)
    onError('')
    try {
      await segmentApi.planShots(projectId, order, force)
      const sh = await segmentApi.shots(projectId, order)
      setShots(sh)
      const est = await segmentApi.planEstimate(projectId, order)
      setEstimate(est)
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    } finally {
      setPlanning(false)
    }
  }

  if (!estimate) return <p className="empty">{copy.loading}</p>

  const seg = estimate.segment
  const locked = seg.has_shots
  const variants = shots
    ? [...shots.variants].sort(
        (a, b) => TIER_ORDER.indexOf(a.tier) - TIER_ORDER.indexOf(b.tier),
      )
    : []

  return (
    <>
      <p className="eyebrow">
        {copy.segments} {seg.order}
      </p>
      <h1>{nameText(seg.title, lang)}</h1>

      <div className="stats">
        <div>
          <div className="stat-label">{copy.scenes}</div>
          <div className="stat-value">{seg.scene_count}</div>
        </div>
        <div>
          <div className="stat-label">{copy.colLength}</div>
          <div className="stat-value">
            {(seg.estimated_seconds / 60).toFixed(1)}
            <span className="unit">{copy.minutes}</span>
          </div>
        </div>
        {shots && (
          <>
            <div>
              <div className="stat-label">{copy.shots}</div>
              <div className="stat-value">{shots.shot_list.clip_count}</div>
            </div>
            <div>
              <div className="stat-label">{copy.veoCalls}</div>
              <div className="stat-value">{shots.shot_list.api_call_count}</div>
            </div>
          </>
        )}
      </div>

      {/* ── 定調 ─────────────────────────────────────────────── */}
      <section className="section">
        <div className="section-head">
          <h2>
          <Icon name="look" size={80} className="section-icon" />
          {copy.lookTitle}
        </h2>
          <span className="section-note">{locked ? copy.lookLocked : '$0'}</span>
        </div>

        {/* ── 這一段用什麼方式講──────────────
            敘事形式是**段落層**的：「搞不好一個劇本裡面好幾種類型混合的，
               搞不好第一章是敘事的，第二章是主演的，是不一定。」
            跟風格剛好相反 —— 風格必須全片一致（角色長相照它生的），
            所以下面的風格按鈕在分鏡後會鎖住，這一排不會。 */}
        {formats.length > 0 && (
          <>
            <h3 className="look-subhead">{copy.segFormatTitle}</h3>
            <div className="choices">
              <button
                className={`choice${format === null ? ' on' : ''}`}
                onClick={() => void chooseFormat(null)}
              >
                <span className="choice-label">{copy.segFormatInherit}</span>
                <span className="choice-hint">{copy.segFormatInheritHint}</span>
              </button>
              {formats.map((f) => (
                <button
                  key={f.id}
                  className={`choice${format === f.id ? ' on' : ''}`}
                  onClick={() => void chooseFormat(f.id)}
                >
                  <span className="choice-label">{t(f.label, lang)}</span>
                  <span className="choice-hint">{t(f.one_liner, lang)}</span>
                </button>
              ))}
            </div>
            {/* 講了「要重新分鏡」就要就地給按鈕。
                叫使用者去做某件事卻不給入口，是最惱人的一種介面。
                但重新分鏡**會作廢這一段的試拍與批次進度** ——
                   代價寫在按鈕旁邊，不是按下去才知道。 */}
            {formatNote && (
              <div className="notice warn">
                <strong>{formatNote}</strong>
                <div className="actions" style={{ marginTop: 12 }}>
                  <button
                    className="btn primary small"
                    onClick={() => void startPlanning(true)}
                    disabled={planning}
                  >
                    {planning ? copy.planning : copy.segFormatReplanButton}
                  </button>
                  <span className="prompts-lang-note">
                    {copy.segFormatReplanCost}
                  </span>
                </div>
              </div>
            )}
            <h3 className="look-subhead">{copy.segStyleTitle}</h3>
          </>
        )}

        <div className="choices">
          {styles.map((p) => {
            const delta = p.difficulty_modifier
            return (
              <button
                key={p.id}
                className={`choice ${p.id === style ? 'on' : ''}`}
                onClick={() => void chooseLook(p.id, ratio)}
                disabled={locked}
              >
                <span className="choice-label">{t(p.label, lang)}</span>
                <span className="choice-hint">{t(p.one_liner, lang)}</span>
                {/* 難度差異直接標在按鈕上 —— 這是本產品最反直覺也最有價值的資訊，
                    不能藏在後端。寫實風最難、動畫風容錯高 15 個百分點。 */}
                <span
                  className={`choice-delta ${delta > 0 ? 'up' : delta < 0 ? 'down' : ''}`}
                >
                  {delta === 0
                    ? '—'
                    : `${delta > 0 ? '+' : ''}${Math.round(delta * 100)}pt`}
                </span>
              </button>
            )
          })}
        </div>

        <div className="choices ratios">
          {ratios.map((r) => (
            <button
              key={r.value}
              className={`choice ${r.value === ratio ? 'on' : ''}`}
              onClick={() => void chooseLook(style, r.value)}
              disabled={locked}
            >
              <span className="choice-label">{t(r.label, lang)}</span>
              <span className="choice-hint">{t(r.hint, lang)}</span>
            </button>
          ))}
        </div>

        {styles.find((p) => p.id === style)?.caution && (
          <div className="notice">
            {t(styles.find((p) => p.id === style)!.caution, lang)}
          </div>
        )}
      </section>

      {/* ── 分鏡：Greenlight Local 改由導演迴圈（場面調度 → 寫稿 → 關卡 → 製作人）做，進度一路顯示 ── */}
      <DirectionPanel
        lang={lang}
        projectId={projectId}
        order={order}
        onDone={() => void (async () => {
          setShots(await segmentApi.shots(projectId, order))
          setEstimate(await segmentApi.planEstimate(projectId, order))
        })()}
      />

      {/* ── 學語言模式：台詞導演（分鏡之後、生成之前 —— H3 唸的是導過的台詞）。沒開學語言模式時自己不顯示 ── */}
      <LinesPanel lang={lang} projectId={projectId} order={order} />

      {/* ── 方案比較 ─────────────────────────────────────────── */}
      {shots && (
        <section className="section">
          <div className="section-head">
            <h2>{copy.variants}</h2>
            <span className="section-note">
              {shots.shot_list.total_seconds}
              {copy.seconds}
            </span>
          </div>

          <div className="scroll">
            <table>
              <thead>
                <tr>
                  <th>{copy.colTier}</th>
                  <th style={{ textAlign: 'right' }}>{copy.colListPrice}</th>
                  <th style={{ textAlign: 'right' }}>{copy.colRetry}</th>
                  <th style={{ textAlign: 'right' }}>{copy.colTotal}</th>
                  <th style={{ textAlign: 'right' }}>{copy.successRate}</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {variants.map((v) => (
                  <tr
                    key={v.tier}
                    className={[
                      v.tier === shots.recommended_tier ? 'recommended' : '',
                      v.tier === shots.chosen_tier ? 'chosen' : '',
                      v.budget_verdict === 'block' ? '' : 'selectable',
                    ]
                      .filter(Boolean)
                      .join(' ')}
                    onClick={() => {
                      // 超出預算的檔位不給選 —— 後端也會擋，這裡是省一次來回
                      if (v.budget_verdict !== 'block') void chooseTier(v.tier)
                    }}
                  >
                    <td className="key">{v.label}</td>
                    <td className="num" style={{ textAlign: 'right' }}>
                      ${v.video_base_usd.toFixed(2)}
                    </td>
                    <td className="num" style={{ textAlign: 'right' }}>
                      ${v.retry_expected_usd.toFixed(2)}
                    </td>
                    <td className="num" style={{ textAlign: 'right' }}>
                      ${v.total_usd.toFixed(2)}
                    </td>
                    <td className="num" style={{ textAlign: 'right' }}>
                      {Math.round(v.success_rate * 100)}%
                    </td>
                    <td>
                      {v.budget_verdict === 'block' && (
                        <span className="badge over">{copy.overBudget}</span>
                      )}
                      {v.tier === shots.chosen_tier && (
                        <span className="badge done">{copy.tierChosen}</span>
                      )}
                      {v.tier === shots.recommended_tier &&
                        v.tier !== shots.chosen_tier && (
                          <span className="badge in-progress">{copy.recommended}</span>
                        )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {!shots.chosen_tier && <p className="tier-hint">{copy.tierHint}</p>}

          {/* 「重拍」那一欄常常比「標價」還貴 —— 這是別的工具看不到的數字 */}
          <div className="notice">
            <strong>{copy.retryNote}</strong>
            <div style={{ marginTop: 8 }}>{t(shots.recommendation_reason, lang)}</div>
          </div>
        </section>
      )}

      {/* ── 風險鏡頭 ─────────────────────────────────────────── */}
      {shots && shots.recommended_tier && (
        <section className="section">
          <div className="section-head">
            <h2>{copy.riskShots}</h2>
            {shots.test_shot_required && (
              <span className="section-note">
                {copy.testShot}: {shots.test_shot_candidate}
              </span>
            )}
          </div>
          {variants
            .find((v) => v.tier === shots.recommended_tier)
            ?.riskiest.map((r) => {
              const shot = shots.shot_list.shots.find((s) => s.shot_id === r.shot_id)
              return (
                <div className="risk" key={r.shot_id}>
                  <div className="risk-head">
                    <span className="risk-rate">
                      {Math.round(r.success_rate * 100)}%
                    </span>
                    <span className="risk-id">{r.shot_id}</span>
                    <span className="risk-desc"><AiText text={shot?.description} lang={lang} projectId={projectId} /></span>
                  </div>
                  <ul className="risk-factors">
                    {r.risk_factors.map((f, i) => (
                      <li key={i}>{t(f, lang)}</li>
                    ))}
                  </ul>
                </div>
              )
            })}
        </section>
      )}

      {/* ── 鏡頭表 ───────────────────────────────────────────── */}
      {shots && (
        <section className="section">
          <div className="section-head">
            <h2>{copy.shotList}</h2>
            <span className="section-note">{shots.shot_list.clip_count}</span>
          </div>
          <div className="scroll">
            <table>
              <thead>
                <tr>
                  <th>{copy.colShotId}</th>
                  <th style={{ textAlign: 'right' }}>{copy.colSeconds}</th>
                  <th>{copy.colFraming}</th>
                  <th>{copy.colMove}</th>
                  <th>{copy.colLink}</th>
                  <th>{copy.colGuards}</th>
                  <th>{copy.colSummary}</th>
                </tr>
              </thead>
              <tbody>
                {shots.shot_list.shots.map((s) => (
                  <tr
                    key={s.shot_id}
                    className="clickable"
                    onClick={() =>
                      setOpenShot(openShot === s.shot_id ? null : s.shot_id)
                    }
                  >
                    <td className="num key">{s.shot_id}</td>
                    <td className="num" style={{ textAlign: 'right' }}>
                      {s.total_seconds}
                      {s.extensions > 0 && (
                        <span style={{ color: 'var(--sumi-faint)' }}>
                          {' '}
                          ({s.base_duration_seconds}+{s.extensions})
                        </span>
                      )}
                    </td>
                    <td className="num">{s.shot_type}</td>
                    <td className="num">{s.camera_move}</td>
                    {/* 約束「套用/匹配」。相等時只顯示一個數字，
                        講不下的時候才變成 6/9 並標紅 ——
                        那個差額就是這一鏡的隱藏風險：規則按預期提升
                        排序取前 6，所以匹配越多、被丟掉的也越多，
                        而匹配多正代表這一鏡本來就難。 */}
                    <td className="num">
                      {s.constraints
                        ? s.constraints.skipped.length > 0
                          ? (
                              <span style={{ color: 'var(--warn, #b4472e)' }}>
                                {s.constraints.applied.length}/
                                {s.constraints.matched_count}
                              </span>
                            )
                          : s.constraints.applied.length
                        : '—'}
                    </td>
                    <td><AiText text={s.description} lang={lang} projectId={projectId} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {openShot && (() => {
            const shot = shots.shot_list.shots.find((s) => s.shot_id === openShot)
            const g = shot?.constraints
            return (
              <>
                <div className="drawer">
                  <div className="drawer-head">{openShot} · prompt</div>
                  {shot?.visual_prompt || copy.noPrompt}
                </div>

                {/* 自己的首幀：人物卡、空景、首幀都可以自己帶；有就原圖釘在第 0 格，優先於接幀 */}
                {!projectId.startsWith('p-sample-') && (
                  <div className="drawer">
                    <div className="drawer-head">{FF[lang].title}</div>
                    {shot?.user_first_frame && (
                      <img src={`/api/projects/${projectId}/media/${shot.user_first_frame.split('/').pop()}/raw?b=${ffBump}`}
                        alt="" style={{ maxWidth: 220, maxHeight: 220, borderRadius: 8, display: 'block', marginBottom: 8 }} />
                    )}
                    <p className="section-note">{FF[lang].note}</p>
                    <div className="actions">
                      <label className="btn small ghost">
                        {FF[lang].upload}
                        <input type="file" accept="image/*" hidden onChange={(e) => {
                          const f = e.target.files?.[0]
                          if (!f) return
                          const fd = new FormData()
                          fd.append('file', f)
                          void send(`/api/projects/${projectId}/segments/${order}/shots/${openShot}/first-frame`, { method: 'POST', body: fd })
                            .then(async () => { setShots(await segmentApi.shots(projectId, order)); setFfBump((b) => b + 1) })
                            .catch((err) => onError(err instanceof Error ? err.message : String(err)))
                        }} />
                      </label>
                      {shot?.user_first_frame && (
                        <button className="btn small ghost" onClick={() => {
                          void send(`/api/projects/${projectId}/segments/${order}/shots/${openShot}/first-frame`, { method: 'DELETE' })
                            .then(async () => setShots(await segmentApi.shots(projectId, order)))
                            .catch((err) => onError(err instanceof Error ? err.message : String(err)))
                        }}>{FF[lang].clear}</button>
                      )}
                    </div>
                  </div>
                )}

                {/* 約束攤開來。在這之前這整段資料算了但沒出過 API ——
                    ConstraintPlan.skipped 的註釋寫著「要顯示出來，
                    不能靜默丟棄」，而它一直在靜默丟棄。 */}
                {g && (g.applied.length > 0 || g.skipped.length > 0) && (
                  <div className="drawer">
                    <div className="drawer-head">
                      {copy.guardsApplied}
                      {g.total_lift > 0 && (
                        <span style={{ color: 'var(--sumi-faint)' }}>
                          {' '}· +{Math.round(g.total_lift * 100)}%
                        </span>
                      )}
                    </div>
                    <ul className="guard-list">
                      {g.applied.map((c) => (
                        <li key={c.rule_id}>
                          <span className="guard-cat">{c.category_label}</span>
                          <span className="guard-principle">{c.principle}</span>
                        </li>
                      ))}
                    </ul>

                    {g.skipped.length > 0 && (
                      <>
                        <div className="drawer-head warn">
                          {copy.guardsSkipped}
                        </div>
                        <p className="guard-note">
                          {copy.guardsCapped
                            .replace('{matched}', String(g.matched_count))
                            .replace('{applied}', String(g.applied.length))}
                        </p>
                        <ul className="guard-list skipped">
                          {g.skipped.map((c) => (
                            <li key={c.rule_id}>
                              <span className="guard-cat">{c.category_label}</span>
                              <span className="guard-principle">{c.principle}</span>
                            </li>
                          ))}
                        </ul>
                      </>
                    )}

                    {g.advisories.length > 0 && (
                      <>
                        <div className="drawer-head">{copy.guardsAdvisory}</div>
                        <ul className="guard-list">
                          {g.advisories.map((a, i) => (
                            <li key={i}>
                              <span className="guard-principle">{a}</span>
                            </li>
                          ))}
                        </ul>
                      </>
                    )}
                  </div>
                )}
              </>
            )
          })()}

          {shots.shot_list.planning_notes.length > 0 && (
            <>
              {shots.shot_list.planning_notes.map((n, i) => (
                <div className={`notice ${n.startsWith('⚠') ? 'warn' : ''}`} key={i}>
                  {sysText(n, lang)}
                </div>
              ))}
            </>
          )}
        </section>
      )}

      {/* 提示詞導出（第 6 關）。只有分鏡完成才有東西可導出。
          放在鏡頭表之後 —— 使用者剛看完「拆成了哪些鏡頭」，
          下一個念頭就是「那實際餵給模型的是什麼」。 */}
      {shots && (
        <PromptsPanel
          copy={copy}
          lang={lang}
          projectId={projectId}
          order={order}
          onError={onError}
        />
      )}

      {/* 選場景（本機 H3）：參考卡要在開拍前準備好 —— 沒有空景卡，沒首幀的鏡只能靠文字猜場景 */}
      {shots && <ScenePanel lang={lang} projectId={projectId} order={order} />}

      {/* 第 7–9 關：試拍 → 批次 → 接片。放在提示詞之後 ——
          成本階梯就是這個順序，畫面順序要跟它一致。 */}
      {shots && (
        <ProductionPanel
          copy={copy}
          lang={lang}
          projectId={projectId}
          order={order}
          onError={onError}
          onOpenCasting={onOpenCasting}
        />
      )}

      {/* ── 成片流程：剪頭 → 放大 → 轉場 → 接片 → 燒字幕（Greenlight Local，services/finish.py）── */}
      <FinishPanel lang={lang} projectId={projectId} order={order} />
    </>
  )
}
