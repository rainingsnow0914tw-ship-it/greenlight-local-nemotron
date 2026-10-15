/**
 * AI 生成的說明文字（製作人意見、選角說明、解析備註、段落摘要）照介面語言顯示。
 *
 * 後端 /api/ui/translate：內容語言跟介面不同才翻（Nemotron Nano，結果存快取），名字照專案名字表。
 * 同一頁的句子合併成一次請求（50ms 內的都併在一起），翻好的記在記憶體；翻好之前先顯示原文。
 * 學語言的台詞與學習筆記不要用這個（那些本來就是多語內容）。
 */
import { useEffect, useState } from 'react'
import type { Lang } from '../api'

type Key = string
const memo = new Map<Key, string>()
const waiting = new Map<string, { texts: string[]; resolvers: ((r: string[]) => void)[] }>()

const k = (lang: Lang, pid: string, text: string) => `${lang}\u0000${pid}\u0000${text}`

function request(lang: Lang, pid: string, text: string): Promise<string> {
  const group = `${lang}\u0000${pid}`
  let g = waiting.get(group)
  if (!g) {
    g = { texts: [], resolvers: [] }
    waiting.set(group, g)
    setTimeout(async () => {
      const batch = waiting.get(group)!
      waiting.delete(group)
      let out: string[] = batch.texts
      try {
        for (let i = 0; i < batch.texts.length; i += 60) {
          const part = batch.texts.slice(i, i + 60)
          const r = await fetch('/api/ui/translate', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ texts: part, lang, project_id: pid || undefined }),
          })
          const got: string[] = r.ok ? (await r.json()).texts : part
          out = i === 0 ? [...got, ...batch.texts.slice(60)] : [...out.slice(0, i), ...got, ...out.slice(i + 60)]
        }
      } catch {
        /* 翻不了就顯示原文 */
      }
      batch.resolvers.forEach((res) => res(out))
    }, 50)
  }
  const idx = g.texts.push(text) - 1
  return new Promise((res) => g!.resolvers.push((all) => res(all[idx] ?? text)))
}

export function useAiText(text: string | null | undefined, lang: Lang, projectId = ''): string {
  const src = text ?? ''
  const [shown, setShown] = useState(() => memo.get(k(lang, projectId, src)) ?? src)
  useEffect(() => {
    if (!src.trim()) { setShown(src); return }
    const key = k(lang, projectId, src)
    const hit = memo.get(key)
    if (hit !== undefined) { setShown(hit); return }
    setShown(src)
    let alive = true
    void request(lang, projectId, src).then((r) => {
      memo.set(key, r)
      if (alive) setShown(r)
    })
    return () => { alive = false }
  }, [src, lang, projectId])
  return shown
}

export function AiText({ text, lang, projectId }: { text: string | null | undefined; lang: Lang; projectId?: string }) {
  return <>{useAiText(text, lang, projectId)}</>
}
