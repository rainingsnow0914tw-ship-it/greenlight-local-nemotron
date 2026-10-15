/**
 * 全片合成 —— 把所有段落的成片接成一部完整的片。
 *
 * 為什麼這一塊在專案層而不是段落層：
 *   段落成片是中間產物，全片才是使用者最後要的東西。
 *   分鏡接成段落只做完一半。
 *
 * 沒有任何段落接過片時整塊不顯示 —— 不要給一個按了只會噴 409 的按鈕。
 */

import { Icon } from './Icons'
import { useEffect, useState } from 'react'
import {
  filmApi,
  t,
  type FilmAssembleResult,
  type Lang,
  type Localized,
  type OutputState,
} from '../api'
import { LANGS } from '../i18n'

interface Props {
  lang: Lang
  projectId: string
  /** 已經接過片的段落數。0 就不顯示這一塊。 */
  readySegments: number
  totalSegments: number
  /** 後端記錄的全片成品。沒有它的話 F5 之後「已合成」就消失。 */
  film: OutputState | null
  onError: (msg: string) => void
}

const COPY: Record<string, Localized> = {
  title: { zh: '合成全片', en: 'Assemble the film', ja: '全編を合成' },
  why: {
    zh: '把各段接成一部完整的片。不重新編碼，畫質不會再掉一次。',
    en: 'Joins every finished segment into one film. No re-encode, no second-generation loss.',
    ja: '各セクションを一本にまとめます。再エンコードしないので画質は落ちません。',
  },
  ready: {
    zh: '{n}/{total} 段已接片',
    en: '{n}/{total} segments ready',
    ja: '{n}/{total} セクション完成',
  },
  run: { zh: '合成全片', en: 'Assemble', ja: '合成する' },
  running: { zh: '合成中…', en: 'Assembling…', ja: '合成中…' },
  download: { zh: '下載全片', en: 'Download film', ja: '全編をダウンロード' },
  subtitles: { zh: '全片字幕', en: 'Film subtitles', ja: '全編字幕' },
  burnTitle: { zh: '把字幕燒進全片', en: 'Burn subtitles into the film', ja: '全編に字幕を焼き込む' },
  burnHint: {
    zh: '整片重新編碼，會跑幾分鐘。原始全片保留。',
    en: 'Re-encodes the whole film — takes a few minutes. The original stays.',
    ja: '全編を再エンコードします（数分）。元ファイルは残ります。',
  },
  burning: { zh: '燒錄中，請不要關掉這一頁…', en: 'Burning — keep this page open…', ja: '焼き込み中。ページを閉じないでください…' },
  burnDone: { zh: '燒好了', en: 'Ready', ja: '完了' },
  stale: {
    zh: '合成之後有段落重接過 —— 這支全片是舊的，重按「合成全片」就會更新（$0）。',
    en: 'A segment was re-assembled after this film was built — it is stale. Re-assemble the film to refresh ($0).',
    ja: '合成後にセクションが更新されています。「合成する」を押し直すと最新になります（$0）。',
  },
}

function fill(s: string, vars: Record<string, string | number>): string {
  return s.replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? ''))
}

export function FilmPanel({
  lang,
  projectId,
  readySegments,
  totalSegments,
  film,
  onError,
}: Props) {
  const [result, setResult] = useState<FilmAssembleResult | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [burned, setBurned] = useState<Record<string, string>>({})

  // 從後端還原「已合成」狀態 —— 這個面板自己不打 API，跟著父層的資料走
  useEffect(() => {
    if (film) {
      setResult((prev) => prev ?? ({ seconds: film.seconds } as FilmAssembleResult))
      setBurned(film.burned)
    }
  }, [film])

  if (readySegments === 0) return null

  const run = async (key: string, fn: () => Promise<unknown>) => {
    if (busy) return
    setBusy(key)
    onError('')
    try {
      await fn()
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="section">
      <div className="section-head">
        <h2>
          <Icon name="assembled" size={80} className="section-icon" />
          {t(COPY.title, lang)}
        </h2>
        <span className="section-note">
          {fill(t(COPY.ready, lang), { n: readySegments, total: totalSegments })}
        </span>
        <span className="price">$0</span>
      </div>
      <p className="prompts-note">{t(COPY.why, lang)}</p>

      <div className="prod-actions">
        <button
          className="btn primary"
          disabled={!!busy}
          onClick={() =>
            void run('assemble', async () => {
              const r = await filmApi.assemble(projectId)
              setResult(r)
              setBurned({})
            })
          }
        >
          {busy === 'assemble' ? t(COPY.running, lang) : t(COPY.run, lang)}
        </button>

        {result && (
          <a className="btn" href={filmApi.downloadUrl(projectId)} download>
            {t(COPY.download, lang)}
            <span className="price">{Math.round(result.seconds)}s</span>
          </a>
        )}
      </div>

      {result?.warning && <div className="notice warn">{result.warning}</div>}

      {/* 某段重接之後，全片就是舊的 —— 正常狀態，但要講 */}
      {film?.stale && !busy && (
        <div className="notice warn">{t(COPY.stale, lang)}</div>
      )}

      {result && (
        <>
          <div className="prompts-lang">
            <span className="prompts-download-label">{t(COPY.subtitles, lang)}</span>
            {LANGS.map((l) => (
              <a
                key={l.code}
                className="btn small"
                href={filmApi.subtitleUrl(projectId, l.code as Lang)}
                download
              >
                {l.label}
              </a>
            ))}
          </div>

          <div className="burn-block">
            <div className="prompts-lang">
              <span className="prompts-download-label">{t(COPY.burnTitle, lang)}</span>
              {LANGS.map((l) => {
                const code = l.code as Lang
                const got = burned[code]
                return got ? (
                  <a key={l.code} className="btn small primary" href={got} download>
                    {l.label} · {t(COPY.burnDone, lang)}
                  </a>
                ) : (
                  <button
                    key={l.code}
                    className="btn small"
                    disabled={!!busy}
                    onClick={() =>
                      void run(`burn-${code}`, async () => {
                        const r = await filmApi.burn(projectId, code)
                        setBurned((prev) => ({ ...prev, [code]: r.download_url }))
                      })
                    }
                  >
                    {busy === `burn-${code}` ? '…' : l.label}
                  </button>
                )
              })}
            </div>
            <p className="prompts-lang-note">
              {busy?.startsWith('burn-')
                ? t(COPY.burning, lang)
                : t(COPY.burnHint, lang)}
            </p>
          </div>
        </>
      )}
    </section>
  )
}
