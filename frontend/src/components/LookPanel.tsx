/**
 * 定調 —— 選視覺風格與畫面比例。**$0。**
 *
 * 為什麼放在專案層：
 *     風格是整片一致的（`ProjectState.style_id`），而且角色候選圖
 *     是照風格生的 —— 真人版與 2D 動畫是兩套完全不同的圖。
 *     若這個選擇只出現在段落頁，使用者可以從專案頁直接進選角、
 *     用預設的寫實風抽一輪，選完才發現風格不對，$0.06 白花。
 *
 *     所以它被抽成獨立元件，放在專案頁角色表之前 —— 讀完本的下一步
 *     就是定調，定調完才輪到選角。段落頁保留同一個元件，改風格不用跑回去。
 */

import { Icon } from './Icons'
import { useCallback, useEffect, useState } from 'react'
import {
  api,
  segmentApi,
  t,
  type AspectRatioOption,
  type Lang,
  type Localized,
  type StylePreset,
} from '../api'

interface Props {
  lang: Lang
  projectId: string
  /** 目前的風格；父層已知就傳進來，避免閃一下預設值。 */
  currentStyle?: string
  /** 目前的敘事形式 */
  currentFormat?: string
  currentRatio?: string
  /** 分鏡做完之後就不能再改（改了鏡頭表的風格設定會對不上）。 */
  locked?: boolean
  onChanged?: (styleId: string, ratio: string) => void
  onError: (msg: string) => void
}

const COPY: Record<string, Localized> = {
  formatTitle: { zh: '用什麼方式講', en: 'How it is told', ja: '語り口' },
  formatWhy: {
    zh: '這決定分鏡的策略：紀錄片以第三人稱旁白為主、畫面負責佐證；劇情片則靠角色的對白推動。跟下面的「看起來像什麼」是兩件事 —— 紀錄片也可以是手繪風。',
    en: 'This drives the shot strategy: a documentary leans on third-person narration with the images as evidence; a drama moves through character dialogue. Separate from the look below — a documentary can still be hand-drawn.',
    ja: 'これが絵コンテの方針を決めます：ドキュメンタリーは第三者のナレーション主体で映像が裏づけ、ドラマは台詞が物語を進めます。下の「見た目」とは別物です。',
  },
  styleTitle: { zh: '看起來像什麼', en: 'How it looks', ja: '見た目' },
  formatReplan: {
    zh: '敘事形式隨時可以改（風格不行 —— 角色圖是照風格生的）。但已經分好鏡的段落不會自動跟著變，要對那一段重新分鏡才會用新策略，一次約 $0.02–0.05。',
    en: 'You can change the narrative format at any time (the visual style is locked, because character images were generated from it). Segments already broken into shots will not update automatically — re-plan that segment to apply the new strategy (about $0.02–0.05).',
    ja: '語り口はいつでも変更できます（見た目は変更不可：キャラクター画像がそれを基に生成されているため）。既に絵コンテ化された段落は自動では更新されません。その段落を再分割すると新しい方針が適用されます（約 $0.02–0.05）。',
  },
  narrationNote: {
    zh: '這個形式以第三人稱旁白為主。旁白**不會**交給影片模型念 —— 系統會用 Gemini TTS 另外配音再混進音軌，所以畫面裡的人不會開口對嘴，而且整段旁白從頭到尾是同一個聲音。想換聲音或語氣，在生成前都還能改。',
    en: 'This format leads with third-person narration. The narration is **not** voiced by the video model — it is synthesised separately with Gemini TTS and mixed in, so nobody on screen lip-syncs and the voice stays identical across every shot.',
    ja: 'この形式は第三者のナレーションが主体です。ナレーションは動画モデルではなく Gemini TTS で別途生成して合成します。そのため画面の人物が口を動かすことはなく、声も全カットで統一されます。',
  },
  title: { zh: '定調', en: 'Set the look', ja: '画調を決める' },
  why: {
    zh: '風格決定角色長什麼樣，所以要在選角之前定。按鈕上的數字是成功率差異 —— 寫實最難，動畫容錯高。',
    en: 'Style decides how characters look, so it comes before casting. The numbers are first-pass success deltas — photoreal is the hardest.',
    ja: '画調はキャラクターの見た目を決めるため、キャスティングより先に選びます。数字は初回成功率の差です。',
  },
  locked: {
    zh: '已定調（分鏡已產生，改風格要先退回）',
    en: 'Locked (shots planned — rewind to change)',
    ja: '確定済み（コンテ作成済み、変更は巻き戻しから）',
  },
}

export function LookPanel({
  lang,
  projectId,
  currentStyle,
  currentRatio,
  currentFormat,
  locked = false,
  onChanged,
  onError,
}: Props) {
  const [styles, setStyles] = useState<StylePreset[]>([])
  const [formats, setFormats] = useState<
    Array<{ id: string; label: Localized; one_liner: Localized }>
  >([])
  const [format, setFormat] = useState(currentFormat || 'drama')
  const [ratios, setRatios] = useState<AspectRatioOption[]>([])
  const [style, setStyle] = useState(currentStyle || 'photoreal')
  const [ratio, setRatio] = useState(currentRatio || '16:9')

  const load = useCallback(async () => {
    try {
      const [s, r, f] = await Promise.all([
        api.styles(),
        api.aspectRatios(),
        api.formats(),
      ])
      setStyles(s.presets)
      setRatios(r.options)
      setFormats(f.formats)
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    }
  }, [onError])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    if (currentStyle) setStyle(currentStyle)
    if (currentRatio) setRatio(currentRatio)
    if (currentFormat) setFormat(currentFormat)
  }, [currentStyle, currentRatio, currentFormat])

  const choose = async (nextStyle: string, nextRatio: string) => {
    setStyle(nextStyle)
    setRatio(nextRatio)
    try {
      await segmentApi.setLook(projectId, {
        style_id: nextStyle,
        aspect_ratio: nextRatio,
        format_id: format,
      })
      onChanged?.(nextStyle, nextRatio)
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    }
  }

  const chooseFormat = async (nextFormat: string) => {
    setFormat(nextFormat)
    try {
      await segmentApi.setLook(projectId, {
        style_id: style,
        aspect_ratio: ratio,
        format_id: nextFormat,
      })
      onChanged?.(style, ratio)
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    }
  }

  if (styles.length === 0) return null

  return (
    <section className="section">
      <div className="section-head">
        <h2>
          <Icon name="look" size={80} className="section-icon" />
          {t(COPY.title, lang)}
        </h2>
        <span className="section-note">
          {locked ? t(COPY.locked, lang) : '$0'}
        </span>
      </div>
      <p className="prompts-note">{t(COPY.why, lang)}</p>

      {/* ── 敘事形式 ─────────────────────
          放在風格之前：它是更上位的決定（用什麼方式講 vs
             看起來像什麼），而且分鏡策略吃它。
          最容易被搞混的是「紀錄片」跟「實拍寫實」不是同一件事 ——
          一部紀錄片可以是實拍，也可以是手繪繪本。所以兩區要分開標題。 */}
      {formats.length > 0 && (
        <>
          <h3 className="look-subhead">{t(COPY.formatTitle, lang)}</h3>
          <p className="prompts-lang-note">{t(COPY.formatWhy, lang)}</p>
          <div className="choices">
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
          {/* 選了以旁白為主的形式，要當場講清楚旁白會走 TTS 配音。
              這條路跟其他鏡頭差很多，不講的話使用者會以為系統漏了台詞。 */}
          {/* 敘事形式不跟著風格鎖 —— 否則分鏡過的專案要重新丟劇本才能改。
              兩者代價差很多：
              改風格要重生所有角色圖，改敘事形式只影響下一次分鏡的策略，
              重新分鏡一次才 $0.02–0.05。把它們綁在同一個 locked 上是偷懶。
              但已經分過鏡的段落不會自動更新，所以要講明什麼時候生效。 */}
          {locked && (
            <p className="prompts-lang-note">{t(COPY.formatReplan, lang)}</p>
          )}
          {(format === 'documentary' || format === 'explainer') && (
            <div className="notice">{t(COPY.narrationNote, lang)}</div>
          )}
          <h3 className="look-subhead">{t(COPY.styleTitle, lang)}</h3>
        </>
      )}

      <div className="choices">
        {styles.map((p) => {
          const delta = p.difficulty_modifier
          return (
            <button
              key={p.id}
              className={`choice ${p.id === style ? 'on' : ''}`}
              onClick={() => void choose(p.id, ratio)}
              disabled={locked}
            >
              <span className="choice-label">{t(p.label, lang)}</span>
              <span className="choice-hint">{t(p.one_liner, lang)}</span>
              {/* 難度差異直接標在按鈕上 —— 這是本產品最反直覺也最有價值的
                  資訊，不能藏在後端。寫實風最難、動畫風容錯高 15 個百分點。 */}
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
            onClick={() => void choose(style, r.value)}
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
  )
}
