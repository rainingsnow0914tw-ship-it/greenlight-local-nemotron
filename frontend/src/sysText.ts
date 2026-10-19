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
  'This is the built-in sample — a finished run to look through, read only. To try it yourself, start a new project with your own script.': {
    zh: '這是內建範例：一次已經完成的流程，只供瀏覽、不能修改。要自己試，請用自己的劇本開一個新專案。',
    en: 'This is the built-in sample — a finished run to look through, read only. To try it yourself, start a new project with your own script.',
    ja: 'これは内蔵サンプルです（完了済みの流れを見るためのもので、変更できません）。試すには、自分の台本で新しいプロジェクトを作ってください。',
  },
  'Directed by Nemotron with pre-render gates and a producer review': {
    zh: '由 Nemotron 導演，生成前過關卡、再經製作人審稿',
    en: 'Directed by Nemotron with pre-render gates and a producer review',
    ja: 'Nemotron が監督、生成前のチェックとプロデューサーのレビュー済み',
  },
}

const PATTERNS: { re: RegExp; tr: (m: RegExpMatchArray) => Tr }[] = [
  // 鏡頭卡上的接幀說明（services/continuity.py、routes/video.py 寫的中文）
  {
    re: /^✓ 接上 (\S+) 的末幀（鏈上第 (\d+)\/(\d+) 鏡）。$/,
    tr: (m) => ({
      zh: m.input ?? m[0],
      en: `✓ Continues from the last frame of ${m[1]} (shot ${m[2]} of up to ${m[3]} in the chain).`,
      ja: `✓ ${m[1]} の最後のフレームから続ける（つなぎ ${m[2]}／最大 ${m[3]} カット）。`,
    }),
  },
  {
    re: /^✓ 斷鏈鏡借 (\S+) 的末幀（同一組人、同一個人正面、已通過）。$/,
    tr: (m) => ({
      zh: m.input ?? m[0],
      en: `✓ Not chained — borrows the last frame of ${m[1]} (same two people, same person facing the camera, already approved).`,
      ja: `✓ つなぎなし — ${m[1]} の最後のフレームを借りる（同じ二人・同じ人が正面・採用済み）。`,
    }),
  },
  {
    // 生成前關卡（routes/video._preflight_gates）：規則說明是給導演模型看的英文，前綴照介面語言
    re: /^(\S+) 沒過生成前關卡，先改分鏡再生：([\s\S]*)$/,
    tr: (m) => ({
      zh: m.input ?? m[0],
      en: `${m[1]} didn't pass the pre-render checks — fix the shot first: ${m[2]}`,
      ja: `${m[1]} は生成前のチェックを通らなかった。先に絵コンテを直してください：${m[2]}`,
    }),
  },
  {
    // 生成一鏡的警告（routes/video.py：改用某一次拍的、首幀從哪來、缺參考圖、無聲版）
    re: /^↺ 已改用第 (\d+) 次拍的版本。$/,
    tr: (m) => ({
      zh: m.input ?? m[0],
      en: `↺ Switched to take ${m[1]}.`,
      ja: `↺ ${m[1]} 回目のテイクに切り替えた。`,
    }),
  },
  {
    re: /^✓ 用你上傳的首幀。$/,
    tr: (m) => ({ zh: m.input ?? m[0], en: '✓ Uses the first frame you uploaded.', ja: '✓ アップロードした最初のコマを使う。' }),
  },
  {
    re: /^⚠ 這一鏡沒有 (.+) 的參考圖，只用文字描述叫模型畫，/,
    tr: (m) => {
      const who = m[1].split('、')
      const en = who.map((w) => (w === '場景' ? 'the set' : w)).join(', ')
      const ja = who.map((w) => (w === '場景' ? '背景' : w)).join('、')
      return {
        zh: m.input ?? m[0],
        en: `⚠ No reference image for ${en} in this shot — the model draws it from text only, so the look or the place may change every time. Upload or prepare reference cards to keep it fixed.`,
        ja: `⚠ このカットには ${ja} の参照画像がなく、文章だけで描かせている —— 見た目や場所が毎回変わるかもしれない。参照カードをアップロードするか用意すると固定できる。`,
      }
    },
  },
  {
    re: /^上一鏡 (\S+) 還沒生成，這一鏡改用硬切。$/,
    tr: (m) => ({ zh: m.input ?? m[0], en: `The previous shot ${m[1]} isn't rendered yet — this shot uses a hard cut.`, ja: `前のカット ${m[1]} がまだ生成されていないので、このカットはハードカットにした。` }),
  },
  {
    re: /^讀不回 (\S+) 的影片，改用硬切。$/,
    tr: (m) => ({ zh: m.input ?? m[0], en: `Couldn't read back the video of ${m[1]} — using a hard cut.`, ja: `${m[1]} の動画を読み込めなかったので、ハードカットにした。` }),
  },
  {
    re: /^這一鏡以無聲版重拍（台詞被內容安全審查擋下）/,
    tr: (m) => ({
      zh: m.input ?? m[0],
      en: 'This shot was re-rendered without sound (the line was blocked by the content safety check) — the line is shown as a subtitle; the script is unchanged.',
      ja: 'このカットは無音版で撮り直した（台詞がコンテンツ安全チェックで止められた）—— 台詞は字幕で見せる。台本は変えていない。',
    }),
  },
  {
    re: /^this plate was made before 2026-10-17 and has no saved prompt/,
    tr: (m) => ({
      zh: '這張空景是 10/17 改版前生的，沒有存提示詞，不能單張重生 —— 按「重新準備參考卡」整組重做。',
      en: m.input ?? m[0],
      ja: 'この空舞台は 10/17 以前に作られたもので、プロンプトが保存されていないため一枚だけ作り直せない —— 「参照カードを作り直す」で全体を作り直して。',
    }),
  },
  {
    re: /^上一鏡 (\S+) 還沒生成，這一鏡改用硬切。$/,
    tr: (m) => ({
      zh: m.input ?? m[0],
      en: `${m[1]} hasn't been generated yet, so this shot starts fresh (hard cut).`,
      ja: `${m[1]} がまだ生成されていないため、このカットは新しく始めます（カット切り替え）。`,
    }),
  },
  {
    re: /^接幀鏈已經連了 (\d+) 鏡/,
    tr: (m) => ({
      zh: m.input ?? m[0],
      en: `The chain has reached ${m[1]} shots — this shot restarts from the character cards so the picture doesn't drift; the previous last frame is kept as a set reference.`,
      ja: `つなぎが ${m[1]} カットに達したため、このカットはキャラクターカードから仕切り直します（画が流れないように）。前のカットの最後のフレームは場面の参考として使います。`,
    }),
  },
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

/** 目前的介面語言（App 切語言時設定）：api.ts 丟出伺服器錯誤之前用它翻成介面語言。 */
let current: Lang = 'en'
export function setUiLang(lang: Lang): void {
  current = lang
}
export function uiText(text: string): string {
  return sysText(text, current)
}

/**
 * 內建範例的名稱（專案名、段落名）照介面語言顯示 —— 範例是附在安裝檔裡給所有人看的，
 * 英文介面不該冒出「第10章 場景61」。只對照範例用到的名稱；使用者自己的專案名照原文。
 */
const SAMPLE_PREFIX: Record<string, Tr> = {
  'Sample — ': { zh: '範例 — ', en: 'Sample — ', ja: 'サンプル — ' },
}
const SAMPLE_NAMES: Record<string, Tr> = {
  '第10章 場景61': { zh: '第10章 場景61', en: 'Chapter 10, Scene 61', ja: '第10章 第61場' },
}

export function nameText(name: string | null | undefined, lang: Lang): string {
  if (!name) return ''
  let out = name
  for (const [p, tr] of Object.entries(SAMPLE_PREFIX)) {
    if (out.startsWith(p)) out = tr[lang] + out.slice(p.length)
  }
  for (const [n, tr] of Object.entries(SAMPLE_NAMES)) {
    if (out.includes(n)) out = out.replace(n, tr[lang])
  }
  return out
}
