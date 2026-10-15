/**
 * 拖放上傳區 —— 整個產品的入口。
 * 直接餵一個劇本檔（MD / TXT / Word / PDF），系統從這裡接手。
 *
 * 設計：劇場的空舞台。一個虛線框，光打在中間，等東西被放上來。
 *
 * 展示版把它停用但不移除：
 *    上傳本身不花錢（只讀檔＋章節偵測），所以展示版的訪客丟得進去、
 *    也會看到章節偵測結果，然後在「解析」那一步撞牆 ——
 *    那個體感是「這東西壞了」，不是「這是展示版」。
 *    中途撞牆比一開始就說清楚糟得多。
 *
 *    但也不整區拿掉：拖放上傳是產品的第一個畫面，
 *    訪客看不到那個入口設計就少了一塊。所以留著、標明、擋住互動。
 */

import { useState, type DragEvent } from 'react'
import { t, type Lang, type Localized } from '../api'
import type { Copy } from '../i18n'

const DEMO_COPY: Record<string, Localized> = {
  badge: { zh: '展示版本', en: 'Demo build', ja: 'デモ版' },
  why: {
    zh: '展示版不呼叫 AI，所以不能上傳新劇本。下面那個專案是真的跑完的 —— 22 個鏡頭、真實花費，ClickHouse 裡每一筆都是實測紀錄。',
    en: 'This demo never calls an AI, so new screenplays cannot be uploaded. The project below is a real completed run — 22 shots, real spend, every ClickHouse row an actual observation.',
    ja: 'デモ版は AI を呼び出さないため、新しい脚本はアップロードできません。下のプロジェクトは実際に完走したもの ——22 カット、実費用、ClickHouse の全行が実測値です。',
  },
  full: {
    zh: '完整版就是從這裡把劇本拖進來開始的。',
    en: 'In the full build, everything starts by dropping a screenplay here.',
    ja: '製品版では、ここに脚本をドロップするところから始まります。',
  },
}

interface Props {
  copy: Copy
  busy: boolean
  onFile: (file: File) => void
  onPick: () => void
  /** 展示版：停用互動並在旁邊說明。預設 false。 */
  demo?: boolean
  lang: Lang
}

export function Dropzone({ copy, busy, onFile, onPick, demo = false, lang }: Props) {
  const [over, setOver] = useState(false)

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setOver(false)
    if (demo) return
    const file = e.dataTransfer.files?.[0]
    if (file) onFile(file)
  }

  return (
    <div className="dropzone-wrap" data-demo={demo}>
      <div
        className="dropzone"
        data-over={over}
        data-busy={busy}
        data-demo={demo}
        aria-disabled={demo}
        onDragOver={(e) => {
          e.preventDefault()
          if (!demo) setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={handleDrop}
        onClick={busy || demo ? undefined : onPick}
        role="button"
        tabIndex={demo ? -1 : 0}
        onKeyDown={(e) => e.key === 'Enter' && !busy && !demo && onPick()}
      >
        {busy ? (
          <p className="dropzone-hint">{copy.reading}</p>
        ) : (
          <>
            <p className="dropzone-hint">{copy.dropHint}</p>
            <button className="btn primary" type="button" disabled={demo}>
              {copy.dropButton}
            </button>
            <p className="dropzone-formats">{copy.dropFormats}</p>
          </>
        )}
      </div>

      {demo && (
        <aside className="dropzone-demo-note">
          <span className="demo-badge">{t(DEMO_COPY.badge, lang)}</span>
          <p>{t(DEMO_COPY.why, lang)}</p>
          <p className="dropzone-demo-full">{t(DEMO_COPY.full, lang)}</p>
        </aside>
      )}
    </div>
  )
}
