/**
 * 後端傳來、直接顯示在畫面上的系統訊息 → 依介面語言顯示（唯一的家）。
 *
 * 後端有些說明是寫好的中文句子（批次為什麼不能開始、選角抽了幾輪…）。介面切到英文或日文時，
 * 這些系統字不能冒出中文（使用者自己的劇本、角色名、學語言的台詞不在此列，那些照原文）。
 * 已知的句子在這裡對照；帶數字的用樣式比對、數字照帶。沒列到的照原文顯示（語言點檢會抓出來補）。
 */
import type { Lang } from './api'

type Tr = Record<Lang, string>

const EXACT: Record<string, Tr> = {
  '要先試拍一鏡並確認通過，才能開始批次生成。': {
    zh: '要先試拍一鏡並確認通過，才能開始批次生成。',
    en: 'Run a test shot and approve it before starting the batch.',
    ja: 'バッチを始める前に、テスト撮影を 1 カット行って承認してください。',
  },
  '還沒進行選角。每個角色都要先選定外貌，生成的人才會長得一樣。': {
    zh: '還沒進行選角。每個角色都要先選定外貌，生成的人才會長得一樣。',
    en: 'Casting has not been done yet. Lock in each character’s look first so they look the same in every shot.',
    ja: 'まだキャスティングしていません。各キャラクターの見た目を先に決めると、全カットで同じ人物になります。',
  },
  'Directed by Nemotron with pre-render gates and a producer review': {
    zh: '由 Nemotron 導演，生成前過關卡、再經製作人審稿',
    en: 'Directed by Nemotron with pre-render gates and a producer review',
    ja: 'Nemotron が監督、生成前のチェックとプロデューサーのレビュー済み',
  },
}

const PATTERNS: { re: RegExp; tr: (m: RegExpMatchArray) => Tr }[] = [
  {
    re: /^已經抽了 (\d+) 輪，花費 \$([\d.]+)。可以繼續，也可以回頭選前面幾輪的圖。$/,
    tr: (m) => ({
      zh: m[0],
      en: `${m[1]} rounds so far, $${m[2]} spent. Keep going, or pick from an earlier round.`,
      ja: `これまで ${m[1]} ラウンド、$${m[2]}。続けるか、前のラウンドから選べます。`,
    }),
  },
  {
    re: /^這個角色已經抽了 (\d+) 輪、花了 \$([\d.]+)。/,
    tr: (m) => ({
      zh: m.input ?? m[0],
      en: `This character has had ${m[1]} rounds ($${m[2]}). That usually means the description is unclear — try another direction, or check how the script describes this character.`,
      ja: `このキャラクターは ${m[1]} ラウンド（$${m[2]}）。説明があいまいな可能性があります。別の方向を試すか、台本の描写を確認してください。`,
    }),
  },
]

/** 段落狀態（後端代號 → 三語） */
export const SEGMENT_STATUS: Record<string, Tr> = {
  not_started: { zh: '尚未開始', en: 'Not started', ja: '未着手' },
  shots_planned: { zh: '已分鏡', en: 'Shots planned', ja: '絵コンテ済み' },
  prompts_ready: { zh: '提示詞已產生', en: 'Prompts ready', ja: 'プロンプト作成済み' },
  test_shot_passed: { zh: '試拍已通過', en: 'Test shot approved', ja: 'テスト撮影承認済み' },
  generating: { zh: '生成中', en: 'Generating', ja: '生成中' },
  done: { zh: '已完成', en: 'Done', ja: '完了' },
}

export function sysText(text: string | null | undefined, lang: Lang): string {
  if (!text) return ''
  const hit = EXACT[text]
  if (hit) return hit[lang]
  for (const p of PATTERNS) {
    const m = text.match(p.re)
    if (m) return p.tr(m)[lang]
  }
  return text
}
