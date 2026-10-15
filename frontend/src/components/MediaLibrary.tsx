/**
 * 媒體庫 —— 一個專案所有生成的圖與影片，逐個管。
 *
 * 設計理由：
 *     「看得到然後用戶選某些可以刪掉就好了，它未必全部一起刪啊。
 *      搞不好好看的留下來，圖片也是一樣道理。醜的先刪除嘛。」
 *
 * 所以每一項獨立卡片、獨立刪除。不做「全選 / 一鍵清空」——
 * 那會鼓勵一次刪一堆，違反這個頁面存在的理由。
 *
 * rewind 與 reset_batch 都不動媒體檔，全部沉澱到這裡。
 *    使用者事後回來挑好留、挑醜刪。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  mediaApi,
  t,
  type Lang,
  type Localized,
  type MediaItem,
  type MediaListResponse,
} from '../api'

interface Props {
  lang: Lang
  projectId: string
  onError: (msg: string) => void
}

const COPY: Record<string, Localized> = {
  title: { zh: '媒體庫', en: 'Media library', ja: 'メディア' },
  subtitle: {
    zh: '這個專案生成的圖與影片。好看的留下、醜的可以逐項刪掉。',
    en: 'Everything this project has generated. Keep the good ones, delete the rest.',
    ja: 'このプロジェクトで生成した画像と映像。良いものは残し、要らないものは個別に削除できます。',
  },
  empty: {
    zh: '還沒有生成任何檔案。跑過選角、試拍或批次之後、這裡就會列出來。',
    en: 'Nothing generated yet. Casting, test shots and batch renders will show up here.',
    ja: 'まだ何も生成されていません。キャスティング・試写・バッチ生成をすると表示されます。',
  },
  loading: { zh: '讀取中…', en: 'Loading…', ja: '読み込み中…' },
  count: { zh: '項', en: 'items', ja: '件' },
  size: { zh: '總大小', en: 'Total', ja: '合計' },
  delete: { zh: '刪除', en: 'Delete', ja: '削除' },
  confirmDelete: {
    zh: '確定要刪掉這個檔案？刪掉之後救不回來。',
    en: 'Delete this file? This cannot be undone.',
    ja: 'このファイルを削除しますか？取り消せません。',
  },
  kindImage: { zh: '圖片', en: 'Image', ja: '画像' },
  kindVideo: { zh: '影片', en: 'Video', ja: '映像' },
  kindOther: { zh: '其他', en: 'Other', ja: 'その他' },
  reload: { zh: '重新整理', en: 'Reload', ja: '再読み込み' },
  deleting: { zh: '刪除中…', en: 'Deleting…', ja: '削除中…' },
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso)
    const pad = (n: number) => String(n).padStart(2, '0')
    return (
      `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
      `${pad(d.getHours())}:${pad(d.getMinutes())}`
    )
  } catch {
    return iso
  }
}

export function MediaLibrary({ lang, projectId, onError }: Props) {
  const [data, setData] = useState<MediaListResponse | null>(null)
  const [deleting, setDeleting] = useState<Set<string>>(new Set())

  const load = useCallback(async () => {
    try {
      const r = await mediaApi.list(projectId)
      setData(r)
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    }
  }, [projectId, onError])

  useEffect(() => {
    void load()
  }, [load])

  const handleDelete = useCallback(
    async (item: MediaItem) => {
      if (!window.confirm(t(COPY.confirmDelete, lang))) return
      setDeleting((prev) => new Set(prev).add(item.filename))
      try {
        await mediaApi.deleteOne(projectId, item.filename)
        // 樂觀 UI：從清單移除、不等 reload
        setData((prev) =>
          prev
            ? {
                ...prev,
                count: Math.max(0, prev.count - 1),
                total_bytes: Math.max(0, prev.total_bytes - item.size_bytes),
                items: prev.items.filter((x) => x.uri !== item.uri),
              }
            : prev,
        )
      } catch (e) {
        onError(e instanceof Error ? e.message : String(e))
      } finally {
        setDeleting((prev) => {
          const next = new Set(prev)
          next.delete(item.filename)
          return next
        })
      }
    },
    [projectId, lang, onError],
  )

  const kindLabel = useMemo(
    () => ({
      image: t(COPY.kindImage, lang),
      video: t(COPY.kindVideo, lang),
      other: t(COPY.kindOther, lang),
    }),
    [lang],
  )

  if (!data) return <p className="empty">{t(COPY.loading, lang)}</p>

  return (
    <>
      <p className="eyebrow">{t(COPY.subtitle, lang)}</p>
      <h1>{t(COPY.title, lang)}</h1>

      <div className="stats">
        <div>
          <div className="stat-label">{t(COPY.count, lang)}</div>
          <div className="stat-value">{data.count}</div>
        </div>
        <div>
          <div className="stat-label">{t(COPY.size, lang)}</div>
          <div className="stat-value">{formatBytes(data.total_bytes)}</div>
        </div>
        <div style={{ alignSelf: 'center' }}>
          <button className="btn ghost" onClick={() => void load()}>
            {t(COPY.reload, lang)}
          </button>
        </div>
      </div>

      {data.items.length === 0 ? (
        <p className="empty" style={{ marginTop: 32 }}>
          {t(COPY.empty, lang)}
        </p>
      ) : (
        <div className="media-grid">
          {data.items.map((item) => {
            const isDeleting = deleting.has(item.filename)
            return (
              <div className="media-card" key={item.uri}>
                <div className="media-preview">
                  {item.kind === 'image' && (
                    <img
                      src={mediaApi.rawUrl(projectId, item.filename)}
                      alt={item.filename}
                      loading="lazy"
                    />
                  )}
                  {item.kind === 'video' && (
                    <video
                      src={mediaApi.rawUrl(projectId, item.filename)}
                      controls
                      preload="metadata"
                    />
                  )}
                  {item.kind === 'other' && (
                    <div className="media-other">📄 {item.filename}</div>
                  )}
                </div>
                <div className="media-meta">
                  <div className="media-filename" title={item.filename}>
                    {item.filename}
                  </div>
                  <div className="media-info">
                    <span>{kindLabel[item.kind]}</span>
                    <span>{formatBytes(item.size_bytes)}</span>
                    <span>{formatDate(item.created_at)}</span>
                  </div>
                  <button
                    className="btn ghost small"
                    onClick={() => void handleDelete(item)}
                    disabled={isDeleting}
                  >
                    {isDeleting ? t(COPY.deleting, lang) : t(COPY.delete, lang)}
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}

      <style>{`
        .media-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
          gap: 18px;
          margin-top: 20px;
        }
        .media-card {
          border: 1px solid var(--edge, rgba(255,255,255,0.08));
          border-radius: 8px;
          overflow: hidden;
          background: var(--panel, rgba(0,0,0,0.2));
          display: flex;
          flex-direction: column;
        }
        .media-preview {
          aspect-ratio: 16 / 9;
          background: #000;
          display: flex;
          align-items: center;
          justify-content: center;
          overflow: hidden;
        }
        .media-preview img,
        .media-preview video {
          width: 100%;
          height: 100%;
          object-fit: contain;
          display: block;
        }
        .media-other {
          padding: 20px;
          color: var(--light-faint);
          font-size: 12px;
          word-break: break-all;
          text-align: center;
        }
        .media-meta {
          padding: 10px 12px;
          display: flex;
          flex-direction: column;
          gap: 6px;
        }
        .media-filename {
          font-size: 12px;
          font-family: var(--mono, monospace);
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .media-info {
          display: flex;
          gap: 8px;
          font-size: 11px;
          color: var(--light-faint);
          flex-wrap: wrap;
        }
        .btn.small { font-size: 11px; padding: 3px 10px; }
      `}</style>
    </>
  )
}
