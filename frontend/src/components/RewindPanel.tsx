/**
 * 退回到前面的階段 —— 兩段式（preview → confirm）。
 *
 * 為什麼要這個：
 *     使用者選完角色才發現「這個故事應該用動畫風」是很正常的事。
 *     不讓他改，他只能整個重來 —— 之前抽的角色圖、寫的提示詞
 *     全變成沉沒成本。
 *
 * 兩段式硬要求：先按 stage 看代價（哪些會失效），
 *    使用者確認之後才真的動。單擊 rewind 沒有 undo。
 *
 * 檔案一律留給媒體庫。這個面板只清 project state，
 *    生成過的圖與影片使用者事後在媒體庫逐項處置。
 */

import { Icon } from './Icons'
import { useCallback, useState } from 'react'
import {
  stateApi,
  t,
  type Lang,
  type Localized,
  type RewindPreview,
} from '../api'

interface Props {
  lang: Lang
  projectId: string
  currentStage: string
  onDone: () => void
  onError: (msg: string) => void
}

/** Stage 順序（跟 backend Stage enum 一致）。用來擋「往前 rewind」的無效選項。 */
const STAGE_ORDER: string[] = [
  'created',
  'script_uploaded',
  'parsed',
  'shots_planned',
  'look_chosen',
  'tier_chosen',
  'cast_locked',
  'prompts_ready',
  'test_shot_passed',
  'generating',
  'assembled',
  'delivered',
]

const STAGE_NAMES: Record<Lang, Record<string, string>> = {
  zh: {
    created: '建立專案',
    script_uploaded: '劇本上傳',
    parsed: '讀本完成',
    shots_planned: '分鏡',
    look_chosen: '定調',
    tier_chosen: '定案',
    cast_locked: '選角',
    prompts_ready: '提示詞',
    test_shot_passed: '試拍',
    generating: '開拍',
    assembled: '成片',
    delivered: '交付',
  },
  en: {
    created: 'Created',
    script_uploaded: 'Script uploaded',
    parsed: 'Parsed',
    shots_planned: 'Shot list',
    look_chosen: 'Look',
    tier_chosen: 'Quality tier',
    cast_locked: 'Cast',
    prompts_ready: 'Prompts',
    test_shot_passed: 'Test shot',
    generating: 'Generating',
    assembled: 'Assembled',
    delivered: 'Delivered',
  },
  ja: {
    created: '作成',
    script_uploaded: '脚本アップロード',
    parsed: '読み込み完了',
    shots_planned: 'コンテ',
    look_chosen: '画調',
    tier_chosen: '画質',
    cast_locked: 'キャスト',
    prompts_ready: 'プロンプト',
    test_shot_passed: '試写',
    generating: '撮影',
    assembled: '編集',
    delivered: '納品',
  },
}

const COPY: Record<string, Localized> = {
  title: {
    zh: '退回到前面的階段',
    en: 'Rewind to an earlier stage',
    ja: '前の段階に戻る',
  },
  intro: {
    zh: '想改風格 / 換角色 / 換分鏡就退回對應的階段。檔案不會刪，留在媒體庫給你事後挑。',
    en: 'Rewind to change look, cast, or shots. Files stay in the media library for you to review later.',
    ja: '画調・キャスト・コンテを変えたいときはその段階まで戻ります。ファイルは削除されず、メディアに残ります。',
  },
  targetLabel: {
    zh: '退到哪個階段',
    en: 'Target stage',
    ja: '戻す段階',
  },
  placeholder: {
    zh: '選一個階段…',
    en: 'Pick a stage…',
    ja: '段階を選択…',
  },
  preview: {
    zh: '看代價',
    en: 'Preview',
    ja: '影響を確認',
  },
  confirm: {
    zh: '確定退回',
    en: 'Confirm rewind',
    ja: '戻す',
  },
  invalidatedTitle: {
    zh: '這樣做會讓下列失效：',
    en: 'This will invalidate:',
    ja: '次のものが無効になります:',
  },
  noInvalidated: {
    zh: '這一段目前沒有失效項目。',
    en: 'Nothing to invalidate.',
    ja: '無効化するものはありません。',
  },
  filesNote: {
    zh: '🔴 檔案不會刪、都留在媒體庫。事後你自己挑好留、挑醜刪。',
    en: '🔴 No files deleted. Everything stays in the media library — sort them yourself.',
    ja: '🔴 ファイルは削除されません。メディアで個別に整理してください。',
  },
  loadingPreview: {
    zh: '計算代價中…',
    en: 'Checking cost…',
    ja: '確認中…',
  },
  committing: {
    zh: '退回中…',
    en: 'Rewinding…',
    ja: '戻しています…',
  },
  doneMsg: {
    zh: '已退回。專案狀態刷新中。',
    en: 'Done. Project state refreshing.',
    ja: '戻しました。状態を更新中。',
  },
}

export function RewindPanel({
  lang,
  projectId,
  currentStage,
  onDone,
  onError,
}: Props) {
  const [open, setOpen] = useState(false)
  const [target, setTarget] = useState<string>('')
  const [preview, setPreview] = useState<RewindPreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [committing, setCommitting] = useState(false)

  const currentIdx = STAGE_ORDER.indexOf(currentStage)
  // 只能退到比當前更早的階段
  const options = STAGE_ORDER.slice(0, Math.max(0, currentIdx))

  const doPreview = useCallback(
    async (targetStage: string) => {
      if (!targetStage) return
      setBusy(true)
      setPreview(null)
      try {
        const p = await stateApi.rewindPreview(projectId, targetStage)
        setPreview(p)
      } catch (e) {
        onError(e instanceof Error ? e.message : String(e))
      } finally {
        setBusy(false)
      }
    },
    [projectId, onError],
  )

  const handleTargetChange = (v: string) => {
    setTarget(v)
    setPreview(null)
    if (v) void doPreview(v)
  }

  const handleCommit = async () => {
    if (!target) return
    setCommitting(true)
    try {
      await stateApi.rewindCommit(projectId, target)
      setOpen(false)
      setTarget('')
      setPreview(null)
      onDone()
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    } finally {
      setCommitting(false)
    }
  }

  if (options.length === 0) return null

  return (
    <section className="section">
      <div className="section-head">
        <h2>
          <Icon name="rewind" size={80} className="section-icon" />
          {t(COPY.title, lang)}
        </h2>
        <button className="btn ghost small" onClick={() => setOpen((v) => !v)}>
          {open ? '−' : '+'}
        </button>
      </div>

      {open && (
        <>
          <p className="hint" style={{ marginBottom: 12 }}>
            {t(COPY.intro, lang)}
          </p>

          <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            <label style={{ fontSize: 12, color: 'var(--light-dim)' }}>
              {t(COPY.targetLabel, lang)}
            </label>
            <select
              value={target}
              onChange={(e) => handleTargetChange(e.target.value)}
              style={{
                padding: '6px 10px',
                background: 'var(--panel, rgba(0,0,0,0.2))',
                color: 'var(--light)',
                border: '1px solid var(--edge, rgba(255,255,255,0.15))',
                borderRadius: 4,
                fontSize: 13,
              }}
            >
              <option value="">{t(COPY.placeholder, lang)}</option>
              {options.map((s) => (
                <option key={s} value={s}>
                  {STAGE_NAMES[lang][s] || s}
                </option>
              ))}
            </select>
          </div>

          {busy && (
            <p className="hint" style={{ marginTop: 10 }}>
              {t(COPY.loadingPreview, lang)}
            </p>
          )}

          {preview && (
            <div
              className="notice"
              style={{ marginTop: 14 }}
            >
              <strong>{t(COPY.invalidatedTitle, lang)}</strong>
              {preview.invalidated.length === 0 ? (
                <p style={{ marginTop: 6 }}>{t(COPY.noInvalidated, lang)}</p>
              ) : (
                <ul style={{ marginTop: 6, marginBottom: 10, paddingLeft: 20 }}>
                  {preview.invalidated.map((line, i) => (
                    <li key={i} style={{ marginBottom: 4 }}>
                      {line}
                    </li>
                  ))}
                </ul>
              )}
              <p style={{ fontSize: 12, color: 'var(--light-faint)' }}>
                {t(COPY.filesNote, lang)}
              </p>
              <button
                className="btn primary"
                style={{ marginTop: 10 }}
                onClick={() => void handleCommit()}
                disabled={committing}
              >
                {committing ? t(COPY.committing, lang) : t(COPY.confirm, lang)}
              </button>
            </div>
          )}
        </>
      )}
    </section>
  )
}
