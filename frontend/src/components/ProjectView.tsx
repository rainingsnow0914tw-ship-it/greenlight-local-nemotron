/**
 * 專案畫面 —— 解析結果、角色、分段、場次。
 *
 * 每個數字都來自真實 API，沒有 mock。
 *
 * 場次表可以點開看原文：行號是模型給的，可能數錯 ——
 * 系統說「第 3 場是這樣」，使用者要能自己核對。
 * 不給對照的機會，等於要人盲信。
 */

import { Icon } from './Icons'
import { AiText } from './AiText'
import { SEGMENT_STATUS, nameText } from '../sysText'
import { useCallback, useEffect, useState } from 'react'
import {
  api,
  t,
  type Lang,
  type Localized,
  type ProjectDetail,
  type Scene,
  type SegmentsResponse,
} from '../api'
import { STAGE_DESCRIPTIONS, STAGE_ORDER, type Copy } from '../i18n'
import { RewindPanel } from './RewindPanel'
import { FilmPanel } from './FilmPanel'
import { LearningPanel } from './LearningPanel'
import { LookPanel } from './LookPanel'
import { LanguagePanel } from './LanguagePanel'

interface Props {
  copy: Copy
  lang: Lang
  projectId: string
  onError: (msg: string) => void
  onOpenSegment: (order: number) => void
  onOpenCasting: () => void
  onOpenMedia: () => void
}

const EXTRA_COPY: Record<string, Localized> = {
  mediaButton: {
    zh: '媒體庫',
    en: 'Media library',
    ja: 'メディア',
  },
}

export function ProjectView({
  copy,
  lang,
  projectId,
  onError,
  onOpenSegment,
  onOpenCasting,
  onOpenMedia,
}: Props) {
  const [detail, setDetail] = useState<ProjectDetail | null>(null)
  const [scenes, setScenes] = useState<Scene[]>([])
  const [segments, setSegments] = useState<SegmentsResponse | null>(null)
  const [openScene, setOpenScene] = useState<number | null>(null)
  const [sourceText, setSourceText] = useState<string>('')
  const [parsing, setParsing] = useState(false)

  const load = useCallback(async () => {
    try {
      const [d, s, g] = await Promise.all([
        api.project(projectId),
        api.scenes(projectId).catch(() => ({ scenes: [] })),
        api.segments(projectId).catch(() => null),
      ])
      setDetail(d)
      setScenes(s.scenes)
      setSegments(g)
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    }
  }, [projectId, onError])

  useEffect(() => {
    void load()
  }, [load])

  const toggleSource = async (n: number) => {
    if (openScene === n) {
      setOpenScene(null)
      return
    }
    try {
      const r = await api.sceneSource(projectId, n)
      setSourceText(r.text)
      setOpenScene(n)
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    }
  }

  if (!detail) return <p className="empty">{copy.loading}</p>

  const sp = detail.screenplay
  const spend = detail.spend
  const plan = segments?.plan

  // 定調過了沒 —— 用 stage 判斷，不能看 style_id。
  //    style_id 有預設值 "photoreal"，「沒選過」跟「選了寫實風」
  //    在資料上長得一模一樣，只有 stage 分得出來。
  const stageIndex = STAGE_ORDER.indexOf(detail.progress.stage)
  const lookReady = stageIndex >= STAGE_ORDER.indexOf('look_chosen')
  // 分鏡做完就不給改風格 —— 鏡頭表帶著當時的風格設定，改了會對不上。
  const lookLocked = stageIndex > STAGE_ORDER.indexOf('tier_chosen')

  return (
    <>
      <p className="eyebrow">
        {STAGE_DESCRIPTIONS[lang][detail.progress.stage] ??
          detail.progress.stage_description}
      </p>
      <h1 className="with-icon">
        <Icon name="script" size={80} className="title-icon" />
        {detail.progress.sample_info?.title?.[lang] ?? nameText(detail.progress.title, lang)}
      </h1>
      {/* 內建範例：這一例示範什麼（三語分開寫，不機翻） */}
      {detail.progress.sample_info?.note?.[lang] && (
        <div className="notice">{detail.progress.sample_info.note[lang]}</div>
      )}

      {/* 上傳完沒馬上解析（回首頁、關掉 app）再打開 → 這裡要有路往下走（試跑：以前只有上傳那一刻看得到解析按鈕，之後就卡死） */}
      {detail.progress.stage === 'script_uploaded' && (
        <div className="actions">
          <button className="btn primary" disabled={parsing} onClick={async () => {
            setParsing(true)
            onError('')
            try {
              const res = await fetch(`/api/projects/${projectId}/parse`, { method: 'POST' })
              const body = await res.json()
              if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`)
              await load()
            } catch (e) {
              onError(e instanceof Error ? e.message : String(e))
            }
            setParsing(false)
          }}>{parsing ? copy.parseWaiting : copy.parseButton}</button>
          <span className="section-note">{copy.parseCostNote}</span>
        </div>
      )}

      <div className="stats">
        {sp && (
          <>
            <div>
              <div className="stat-label">{copy.scenes}</div>
              <div className="stat-value">{sp.scene_count}</div>
            </div>
            <div>
              <div className="stat-label">{copy.characters}</div>
              <div className="stat-value">{sp.characters.length}</div>
            </div>
            <div>
              <div className="stat-label">{copy.colLength}</div>
              <div className="stat-value">
                {(sp.total_seconds / 60).toFixed(1)}
                <span className="unit">{copy.minutes}</span>
              </div>
            </div>
          </>
        )}
        <div>
          <div className="stat-label">{copy.spent}</div>
          <div className="stat-value">
            ${spend.total_usd.toFixed(2)}
            <span className="unit">/ ${spend.ceiling_usd}</span>
          </div>
        </div>
        <div style={{ alignSelf: 'center' }}>
          <button className="btn ghost" onClick={onOpenMedia}>
            {t(EXTRA_COPY.mediaButton, lang)}
          </button>
        </div>
      </div>

      {/* 內建範例（services/sample.py）：讓人一眼知道這是示範、可以直接看過程與結果 */}
      {projectId.startsWith('p-sample-') && (
        <div className="notice">
          {{
            zh: '這是內建範例：用 Greenlight Local 完整跑完一次的流程，只供瀏覽、不能修改。每一步的過程和結果都在這裡：導演回合、查證與診斷、學語言台詞、成片。要自己做，請回首頁用自己的劇本開新專案（安裝與使用說明在 README）。',
            en: 'Built-in sample: one complete run of Greenlight Local, read only. Every step and its result is here to look through: the director’s rounds, shot checks and diagnosis, study-mode lines, and the finished film. To make your own, go back and start a new project with your own script (setup and usage are in the README).',
            ja: '内蔵サンプル：Greenlight Local で一通り作り終えた流れです（閲覧のみ、変更はできません）。各ステップの過程と結果をここで見られます：監督のラウンド、検証と診断、学習用のセリフ、完成映像。自分で作るには、ホームに戻って自分の台本で新しいプロジェクトを始めてください（導入と使い方は README にあります）。',
          }[lang]}
        </div>
      )}

      {sp && sp.warnings.length > 0 && (
        <section className="section">
          <div className="section-head">
            <h2>
          <Icon name="caution" size={80} className="section-icon" />
          {copy.warnings}
        </h2>
          </div>
          {sp.warnings.map((w, i) => (
            <div className="notice" key={i}>
              <AiText text={w} lang={lang} projectId={projectId} />
            </div>
          ))}
        </section>
      )}


      {/* 定調 —— 必須排在角色表之前。
          沒定風格就沒法選角色（真人還是動漫風）：
          角色圖是照風格生的，順序不能顛倒。 */}
      {sp && (
        <LookPanel
          lang={lang}
          projectId={projectId}
          // 已存的值一定要傳進去（否則選了紀錄片會跳回預設）：
          //    沒傳的話 LookPanel 每次掛上都用預設值（劇情片／寫實／16:9）顯示，
          //    使用者再按任何一顆風格按鈕，面板就把畫面上那個預設的 format 一起送回去，
          //    把後端存好的「紀錄片」蓋掉。回滾不是後端忘了存，是前端拿預設值把它覆寫了。
          currentStyle={detail.progress.style_id}
          currentRatio={detail.progress.aspect_ratio}
          currentFormat={detail.progress.format_id}
          locked={lookLocked}
          onChanged={() => void load()}
          onError={onError}
        />
      )}

      {/* 學語言模式：原聲語言＋字幕語言。跟風格一樣是整片一致的決定，要在導台詞之前定 */}
      {sp && <LanguagePanel lang={lang} projectId={projectId} />}

      {/* 角色 */}
      {sp && sp.characters.length > 0 && (
        <section className="section">
          <div className="section-head">
            <h2>
          <Icon name="casting" size={80} className="section-icon" />
          {copy.characters}
        </h2>
            <span className="section-note">{sp.characters.length}</span>
            {/* 選角入口放在角色表旁邊 —— 使用者剛看完「有哪些角色」，
                下一個念頭就是「他們長什麼樣」。
                但沒定調就不給進：角色圖是照風格生的，
                   先抽再改風格 = 那些圖全部作廢。 */}
            <button
              className="btn primary small"
              onClick={onOpenCasting}
              disabled={!lookReady}
              title={lookReady ? undefined : '先在上面選好風格與比例'}
            >
              {copy.castingOpenButton}
            </button>
          </div>
          {!lookReady && (
            <div className="notice">
              先在上面「定調」選好風格與畫面比例，再開始選角 ——
              角色的長相是照風格生的，真人版與動畫版是兩套完全不同的圖。
            </div>
          )}
          <div className="scroll">
            <table>
              <thead>
                <tr>
                  <th>{copy.characters}</th>
                  <th></th>
                  <th style={{ textAlign: 'right' }}>{copy.colLines}</th>
                  <th>{copy.colAppearance}</th>
                </tr>
              </thead>
              <tbody>
                {sp.characters.map((ch) => (
                  <tr key={ch.name}>
                    <td className="key">{ch.name}</td>
                    <td>{ch.is_principal ? copy.principal : copy.supporting}</td>
                    <td className="num" style={{ textAlign: 'right' }}>
                      {ch.line_count}
                    </td>
                    <td
                      style={{
                        color: ch.visual_description
                          ? 'var(--light-dim)'
                          : 'var(--light-faint)',
                      }}
                    >
                      {ch.visual_description || copy.noDescription}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* 分段 */}
      {plan && (
        <section className="section">
          <div className="section-head">
            <h2>
          <Icon name="segments" size={80} className="section-icon" />
          {nameText(plan.label, lang)}
        </h2>
            <span className="section-note">
              {(plan.total_seconds / 60).toFixed(0)}
              {copy.minutes} · ${plan.estimated_total_usd} ({plan.tier})
            </span>
          </div>

          {segments!.continuity_problems.map((p, i) => (
            <div className="notice warn" key={i}>
              {p}
            </div>
          ))}

          <div className="segments">
            {plan.segments.map((s) => (
              <div
                className="segment"
                key={s.segment_id}
                role="button"
                tabIndex={0}
                onClick={() => onOpenSegment(s.order)}
                onKeyDown={(e) => e.key === 'Enter' && onOpenSegment(s.order)}
              >
                <div className="segment-no">{s.order}</div>
                <div>
                  <p className="segment-title">{nameText(s.title, lang)}</p>
                  <p className="segment-synopsis"><AiText text={s.synopsis} lang={lang} projectId={projectId} /></p>
                  <span className={`badge ${s.status === 'done' ? 'done' : 'not-started'}`}>
                    {SEGMENT_STATUS[s.status]?.[lang] ?? s.status_label}
                  </span>
                </div>
                <div className="segment-meta">
                  <div className="segment-cost">${s.estimated_usd.toFixed(2)}</div>
                  <div>
                    {(s.estimated_seconds / 60).toFixed(1)}
                    {copy.minutes}
                  </div>
                  <div style={{ color: 'var(--light-faint)' }}>
                    {s.scene_numbers.length}
                    {copy.scenesUnit}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* 學習迴路 —— ClickHouse 學到了什麼，攤在看得見的地方 */}
      <LearningPanel lang={lang} projectId={projectId} />

      {/* 全片合成 —— 段落之後的最後一步。
          沒有任何段落接過片時 FilmPanel 自己不顯示。 */}
      {plan && (
        <FilmPanel
          lang={lang}
          projectId={projectId}
          readySegments={plan.segments.filter((s) => s.status === 'done').length}
          totalSegments={plan.segments.length}
          film={segments?.film ?? null}
          onError={onError}
        />
      )}

      {/* 場次表 */}
      {scenes.length > 0 && (
        <section className="section">
          <div className="section-head">
            <h2>
          <Icon name="parsed" size={80} className="section-icon" />
          {copy.scenes}
        </h2>
            <span className="section-note">{scenes.length}</span>
          </div>
          <div className="scroll">
            <table>
              <thead>
                <tr>
                  <th>{copy.colScene}</th>
                  <th>{copy.colLocation}</th>
                  <th>{copy.colTime}</th>
                  <th style={{ textAlign: 'right' }}>{copy.colSeconds}</th>
                  <th>{copy.colSummary}</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {scenes.map((s) => (
                  <tr key={s.scene_number}>
                    <td className="num key">{s.scene_number}</td>
                    <td>{s.location ? <AiText text={s.location} lang={lang} projectId={projectId} /> : '—'}</td>
                    <td className="num">{s.time_of_day}</td>
                    <td className="num" style={{ textAlign: 'right' }}>
                      {s.estimated_seconds.toFixed(0)}
                    </td>
                    <td><AiText text={s.action} lang={lang} projectId={projectId} /></td>
                    <td>
                      <button
                        className="btn ghost"
                        style={{ fontSize: 11, padding: '2px 0' }}
                        onClick={() => void toggleSource(s.scene_number)}
                      >
                        {openScene === s.scene_number
                          ? copy.hideSource
                          : copy.viewSource}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {openScene != null && (
            <div className="drawer">
              <div className="drawer-head">
                {copy.colSource} · {copy.colScene} {openScene}
              </div>
              {sourceText}
            </div>
          )}
        </section>
      )}

      {/* 進階：退回到前面的階段。放最下面 —— 是修正動作、不是主流程。
          檔案不會刪，都留在媒體庫給使用者事後挑。 */}
      <RewindPanel
        lang={lang}
        projectId={projectId}
        currentStage={detail.progress.stage}
        onDone={() => void load()}
        onError={onError}
      />
    </>
  )
}
