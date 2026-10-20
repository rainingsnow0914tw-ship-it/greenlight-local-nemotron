/**
 * 提示詞導出（第 6 關）。
 *
 * 選好角色之後先出完全部提示詞，使用者可以拿去別的 AI 試，
 * 然後才進下一步。
 *
 * 這一關全部 $0 —— 提示詞是 code 組裝的，不呼叫任何模型。
 *
 * 為什麼要讓人把提示詞帶走：
 *   1. 免費檢查點：在這裡發現「主角描述根本不對」花 $0，生成後才發現花 $18
 *   2. 不鎖定使用者：敢讓人帶著成果離開的工具，比綁著他的更值得信任
 *   3. 透明度：提示詞是這個系統的核心產出，藏起來等於要人盲信
 */

import { Icon } from './Icons'
import { Gloss } from './AiText'
import { useCallback, useEffect, useState } from 'react'
import { promptsApi, type Lang, type PromptsResponse, type ShotPrompt } from '../api'
import { LANGS, PROMPT_SECTIONS, type Copy } from '../i18n'

interface Props {
  copy: Copy
  lang: Lang
  projectId: string
  order: number
  onError: (msg: string) => void
}

export function PromptsPanel({ copy, lang, projectId, order, onError }: Props) {
  const [data, setData] = useState<PromptsResponse | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const [copied, setCopied] = useState<string | null>(null)
  const [rewriting, setRewriting] = useState<Lang | null>(null)

  const load = useCallback(async () => {
    try {
      setData(await promptsApi.list(projectId, order))
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    }
  }, [projectId, order, onError])

  useEffect(() => {
    void load()
  }, [load])

  if (!data) return null

  const switchLanguage = async (target: Lang) => {
    if (target === data.prompt_language || rewriting) return
    setRewriting(target)
    onError('')
    try {
      await promptsApi.rewrite(projectId, order, target)
      // 改寫是就地修改 shot_list，所以要重新拉一次才看得到新內容
      await load()
      // 展開中的那一鏡收起來 —— 內容已經換掉了，
      // 讓使用者重新點開，避免以為看到的還是舊的。
      setOpen(null)
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    } finally {
      setRewriting(null)
    }
  }

  const copyPrompt = async (prompt: ShotPrompt) => {
    try {
      await navigator.clipboard.writeText(prompt.full)
      setCopied(prompt.shot_id)
      // 兩秒後把「已複製」收回去 —— 一直留著會讓人不確定
      // 最後複製的到底是哪一鏡。
      setTimeout(() => setCopied((v) => (v === prompt.shot_id ? null : v)), 2000)
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <section className="section prompts">
      <div className="section-head">
        <h2>
          <Icon name="prompts" size={80} className="section-icon" />
          {copy.promptsTitle}
        </h2>
        <span className="section-note">{data.shot_count}</span>
        <span className="price">$0</span>
      </div>

      <p className="prompts-note">{copy.promptsNote}</p>

      {/* 只點名這一段真正還缺的角色 —— 籠統的警告會讓人以為整份都不能用 */}
      {data.casting_warning && (
        <div className="notice warn">{data.casting_warning}</div>
      )}

      {/* 提示詞語言。零輸入鐵律：三個按鈕，不打字。
          這跟左上角的介面語言是**不同的東西** —— 那個管使用者看到的文字，
             這個管餵給模型的畫面描述。所以放在提示詞區塊裡而不是頂端。 */}
      <div className="prompts-lang">
        <span className="prompts-download-label">{copy.promptsLanguage}</span>
        {LANGS.map((l) => {
          const isCurrent = l.code === data.prompt_language
          return (
            <button
              key={l.code}
              className={`btn small${isCurrent ? ' on' : ''}`}
              onClick={() => void switchLanguage(l.code)}
              disabled={isCurrent || rewriting !== null}
            >
              {l.label}
              {isCurrent && (
                <span className="price">{copy.promptsCurrentLanguage}</span>
              )}
            </button>
          )
        })}
        {rewriting && <span className="prompts-rewriting">{copy.promptsRewriting}</span>}
      </div>
      <p className="prompts-lang-note">{copy.promptsLanguageNote}</p>

      <div className="prompts-download">
        <span className="prompts-download-label">{copy.promptsDownload}</span>
        {(['text', 'markdown', 'json'] as const).map((fmt) => (
          <a
            key={fmt}
            className="btn small"
            href={promptsApi.downloadUrl(projectId, order, fmt)}
            download
          >
            {fmt === 'text' ? 'TXT' : fmt === 'markdown' ? 'MD' : 'JSON'}
          </a>
        ))}
      </div>

      <div className="prompts-list">
        {data.prompts.map((p) => (
          <div className="prompt-row" key={p.shot_id}>
            <div className="prompt-head">
              <span className="prompt-id">{p.shot_id}</span>
              <span className="prompt-len">
                {p.duration_seconds}
                {copy.seconds}
                {p.extensions > 0 && ` (${p.base_duration_seconds}+${p.extensions})`}
              </span>
              {/* 旁白鏡要看得出來是 TTS 配音。這條路跟其他鏡頭差很多 —— 台詞不進影片模型、
                  由 TTS 另外配音再混音 —— 畫面上不講的話，使用者只會覺得
                  「提示詞裡怎麼沒有台詞」。 */}
              {p.voice_role === 'narrator' && (
                <span className="tag alt">{copy.promptsNarration}</span>
              )}
              {p.voice_role === 'offscreen' && (
                <span className="tag">{copy.promptsOffscreen}</span>
              )}
              <span className="prompt-preview">{p.segments.description || p.full}<Gloss text={p.segments.description || p.full} lang={lang} projectId={projectId} /></span>
              <button
                className="btn small"
                onClick={() => void copyPrompt(p)}
              >
                {copied === p.shot_id ? copy.promptsCopied : copy.promptsCopy}
              </button>
              <button
                className="btn ghost small"
                onClick={() => setOpen(open === p.shot_id ? null : p.shot_id)}
              >
                {open === p.shot_id ? copy.promptsHideFull : copy.promptsShowFull}
              </button>
            </div>

            {open === p.shot_id && (
              <div className="prompt-body">
                {Object.entries(p.segments)
                  .filter(([, text]) => text)
                  .map(([key, text]) => (
                    <div className="prompt-section" key={key}>
                      <span className="prompt-section-label">
                        {/* 缺語言退回中文，不要讓整棵樹掛掉：
                            PROMPT_SECTIONS[lang] 為 undefined 時直接取 [key]
                            會拋 TypeError，React 沒有 error boundary 就整頁白掉 ——
                            連首頁都不見了，看起來像後端掛了，其實只是缺一個翻譯。 */}
                        {(PROMPT_SECTIONS[lang] ?? PROMPT_SECTIONS.zh)[key] ?? key}
                      </span>
                      <p>{text}</p>
                    </div>
                  ))}

                {p.applied_constraints.length > 0 && (
                  <div className="prompt-constraints">
                    <span className="prompt-section-label">
                      {copy.promptsConstraints}
                    </span>
                    <ul>
                      {p.applied_constraints.map((c) => (
                        <li key={c.rule_id}>
                          <strong>{c.category}</strong>
                          {c.principle}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {p.advisories.map((a, i) => (
                  <div className="notice warn" key={i}>
                    {a}
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}
