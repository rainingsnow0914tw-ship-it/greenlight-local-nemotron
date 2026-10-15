/**
 * 手繪風 icon 與 Logo。
 *
 * 為什麼是手寫 SVG 而不是圖檔或 icon 套件：
 *    整站走手繪風格，而手寫路徑的三個好處都是圖檔沒有的 ——
 *    向量任意縮放、顏色跟站上的和紙配色連動（改一個 CSS 變數全部跟著變）、
 *    產生它 $0（生圖要錢，而且每次生的都不一樣，做不成一套識別）。
 *
 * 手繪感是四件事疊出來的，少一件就會變成「乾淨但無聊」的線性 icon：
 *    1. 線條分兩層 —— 外框 2.4、內部細節 1.5。粗細一致最像機器畫的。
 *    2. 色塊不對齊線稿 —— 淡彩故意偏移一兩個 px，手上色沒對準的味道。
 *    3. 輕微抖動 —— feTurbulence 位移 0.8。
 *       不要調到 1.6 以上：位移太大會把線推糊，看起來隨便，糊 ≠ 手繪。
 *    4. 多餘的裝飾 —— 書籤、顏料滴、拍板上的紅燈、迴圈上的節點。
 *       手繪 logo 耐看就是因為有這些「不必要」的東西。
 *
 * 濾鏡的位移量不隨尺寸縮放：20px 以下請傳 `plain`，
 *    否則邊緣會被推爛（rail 的小 icon 與 favicon 都走這條）。
 */

/** 濾鏡定義。整個 app 掛一次就好，id 前綴 `gl-` 避開 App.tsx 既有的鳥居濾鏡。 */
export function IconDefs() {
  return (
    <svg width="0" height="0" style={{ position: 'absolute' }} aria-hidden="true">
      <defs>
        {/* 水彩推邊：淡彩色塊用，跟站上鳥居同一套手法 */}
        <filter id="gl-wash" x="-20%" y="-20%" width="140%" height="140%">
          <feTurbulence
            type="fractalNoise"
            baseFrequency="0.028"
            numOctaves="4"
            seed="7"
            result="g"
          />
          <feDisplacementMap
            in="SourceGraphic"
            in2="g"
            scale="3.2"
            xChannelSelector="R"
            yChannelSelector="G"
          />
        </filter>
        {/* 手繪線：只讓線不平直，不推糊（見檔頭第 3 點） */}
        <filter id="gl-hand" x="-10%" y="-10%" width="120%" height="120%">
          <feTurbulence
            type="fractalNoise"
            baseFrequency="0.042"
            numOctaves="2"
            seed="3"
            result="g"
          />
          <feDisplacementMap
            in="SourceGraphic"
            in2="g"
            scale="0.8"
            xChannelSelector="R"
            yChannelSelector="G"
          />
        </filter>
      </defs>
    </svg>
  )
}

export type IconName =
  | 'script'
  | 'parsed'
  | 'shots'
  | 'look'
  | 'tier'
  | 'casting'
  | 'prompts'
  | 'testshot'
  | 'generating'
  | 'assembled'
  | 'screening'
  | 'subtitles'
  | 'learning'
  | 'media'
  | 'caution'
  | 'segments'
  | 'rewind'

/** 十個關卡對應的 icon。key 跟 i18n 的 STAGE_ORDER 一致。 */
export const STAGE_ICONS: Record<string, IconName> = {
  script_uploaded: 'script',
  parsed: 'parsed',
  shots_planned: 'shots',
  look_chosen: 'look',
  tier_chosen: 'tier',
  cast_locked: 'casting',
  prompts_ready: 'prompts',
  test_shot_passed: 'testshot',
  generating: 'generating',
  assembled: 'assembled',
}

interface IconProps {
  name: IconName
  size?: number
  /**
   * 關掉濾鏡與淡彩。
   * 20px 以下一定要開 —— 濾鏡位移量固定，小尺寸會把邊緣推爛。
   */
  plain?: boolean
  className?: string
}

/**
 * 畫一個手繪 icon。
 *
 * Args:
 *   name: 要哪一個（見 IconName）
 *   size: 邊長 px，預設 24
 *   plain: 小尺寸模式 —— 拿掉濾鏡與淡彩，只留線稿
 *
 * Returns:
 *   一個 64×64 viewBox 的 <svg>，顏色全部走 CSS 變數。
 */
export function Icon({ name, size = 24, plain = false, className }: IconProps) {
  const hand = plain ? undefined : 'url(#gl-hand)'
  const wash = plain ? undefined : 'url(#gl-wash)'
  // 小尺寸下淡彩會糊成一團色斑，不如不要
  const tint = plain ? 0 : 1

  return (
    <svg
      viewBox="0 0 64 64"
      width={size}
      height={size}
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      {ICON_BODIES[name](hand, wash, tint)}
    </svg>
  )
}

type Body = (
  hand: string | undefined,
  wash: string | undefined,
  tint: number,
) => JSX.Element

/**
 * 每個 icon 的畫法。
 *
 * 線條一律 `var(--sumi)`（外框）與 `var(--sumi-soft)`（細節），
 *    不要寫死顏色 —— 全站墨色會為了對比度調整，
 *    寫死的話這裡會變成唯一沒跟上的地方。
 */
const ICON_BODIES: Record<IconName, Body> = {
  // ── 劇本：書頁 + 書籤 ──
  script: (hand, wash, tint) => (
    <>
      {tint > 0 && (
        <path
          d="M21,13 h25 a3,3 0 0 1 3,3 v33 a3,3 0 0 1 -3,3 h-25 a4,4 0 0 1 -4,-4 v-31 a4,4 0 0 1 4,-4z"
          fill="var(--sakura-pale)"
          filter={wash}
        />
      )}
      <g filter={hand}>
        <g
          fill="none"
          stroke="var(--sumi)"
          strokeWidth="2.4"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M22,11 h25 a3,3 0 0 1 3,3 v33 a3,3 0 0 1 -3,3 h-25" />
          <path d="M22,11 a4,4 0 0 0 -4,4 v31 a4,4 0 0 0 4,4" />
        </g>
        <g fill="none" stroke="var(--sumi-soft)" strokeWidth="1.5" strokeLinecap="round">
          <path d="M27,21 h16 M27,28 h16 M27,35 h9" />
        </g>
      </g>
      <path d="M41,11 v13 l-3.5,-3.5 -3.5,3.5 v-13z" fill="var(--shu)" filter={hand} />
    </>
  ),

  // ── 解析：把整份稿子拆成一條一條的場次 ──
  parsed: (hand, wash, tint) => (
    <>
      {tint > 0 && (
        <rect x="12" y="14" width="18" height="36" rx="2.5" fill="var(--fuji-pale)" filter={wash} />
      )}
      <g filter={hand}>
        <rect
          x="11"
          y="13"
          width="18"
          height="37"
          rx="2.5"
          fill="none"
          stroke="var(--sumi)"
          strokeWidth="2.4"
          strokeLinejoin="round"
        />
        <g fill="none" stroke="var(--sumi-soft)" strokeWidth="1.5" strokeLinecap="round">
          <path d="M15,20 h10 M15,26 h10 M15,32 h7" />
        </g>
        <g fill="none" stroke="var(--sumi)" strokeWidth="2.2" strokeLinecap="round">
          <path d="M36,19 h16 M36,31 h16 M36,43 h11" />
        </g>
      </g>
      {/* 裝飾：每一條場次前的印點，顏色不同代表被分出來的段 */}
      <circle cx="33" cy="19" r="2.1" fill="var(--shu)" />
      <circle cx="33" cy="31" r="2.1" fill="var(--kohaku)" />
      <circle cx="33" cy="43" r="2.1" fill="var(--fuji)" />
    </>
  ),

  // ── 分鏡：兩格畫面 + 接續的虛線 ──
  shots: (hand, wash, tint) => (
    <>
      {tint > 0 && (
        <>
          <rect x="12" y="19" width="19" height="15" rx="2" fill="var(--fuji)" opacity="0.55" filter={wash} />
          <rect x="34" y="30" width="19" height="15" rx="2" fill="var(--kohaku)" opacity="0.45" filter={wash} />
        </>
      )}
      <g filter={hand}>
        <g fill="none" stroke="var(--sumi)" strokeWidth="2.4" strokeLinejoin="round">
          <rect x="11" y="18" width="19" height="16" rx="2.5" />
          <rect x="34" y="29" width="19" height="16" rx="2.5" />
        </g>
        <g fill="none" stroke="var(--sumi-soft)" strokeWidth="1.5" strokeLinecap="round">
          <path d="M15,29 l4,-5 3,3 3.5,-4" />
          <path d="M38,40 l4,-5 3,3 3.5,-4" />
          <path d="M30,26 c3,0 3,6 6,6" strokeDasharray="2.5 3" />
        </g>
      </g>
      <circle cx="17" cy="23" r="1.8" fill="var(--kohaku)" />
    </>
  ),

  // ── 定調：筆刷 + 兩坨顏料 ──
  look: (hand, wash, tint) => (
    <>
      {tint > 0 && (
        <>
          <circle cx="24" cy="30" r="11" fill="var(--sakura)" opacity="0.75" filter={wash} />
          <circle cx="36" cy="37" r="9" fill="var(--fuji)" opacity="0.65" filter={wash} />
        </>
      )}
      <g filter={hand}>
        <path
          d="M44,14 c-3.5,-3.5 -9,-2.5 -11,2 l-14,26 c-1.8,3.5 0.6,7 4,6.2 l6.5,-1.5"
          fill="none"
          stroke="var(--sumi)"
          strokeWidth="2.4"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <path d="M41,19 l5,4.5" fill="none" stroke="var(--sumi-soft)" strokeWidth="1.5" strokeLinecap="round" />
      </g>
      <circle cx="47" cy="44" r="2.6" fill="var(--shu)" opacity="0.8" filter={wash} />
      <circle cx="52" cy="37" r="1.6" fill="var(--kohaku)" opacity="0.8" />
    </>
  ),

  // ── 定案：三階成本階梯，選中的那階打勾 ──
  tier: (hand, wash, tint) => (
    <>
      {tint > 0 && (
        <>
          <rect x="12" y="38" width="12" height="12" fill="var(--fuji)" opacity="0.5" filter={wash} />
          <rect x="26" y="28" width="12" height="22" fill="var(--sakura)" opacity="0.6" filter={wash} />
          <rect x="40" y="16" width="12" height="34" fill="var(--kohaku)" opacity="0.4" filter={wash} />
        </>
      )}
      <g filter={hand} fill="none" stroke="var(--sumi)" strokeWidth="2.4" strokeLinejoin="round">
        <rect x="11" y="37" width="12" height="13" />
        <rect x="26" y="27" width="12" height="23" />
        <rect x="41" y="15" width="12" height="35" />
      </g>
      {/* 裝飾：選中的是中間那階（推薦邏輯就是不選最貴） */}
      <circle cx="32" cy="21" r="6" fill="var(--shu)" filter={wash} />
      <path
        d="M29.5,21 l2,2 3.5,-4"
        fill="none"
        stroke="var(--washi)"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </>
  ),

  // ── 選角：人像 + 選定的印記 ──
  casting: (hand, wash, tint) => (
    <>
      {tint > 0 && (
        <>
          <circle cx="32" cy="24" r="8.5" fill="var(--kohaku)" opacity="0.5" filter={wash} />
          <path
            d="M19,48 c2,-9.5 7,-13.5 13,-13.5 c6,0 11,4 13,13.5z"
            fill="var(--sakura)"
            opacity="0.6"
            filter={wash}
          />
        </>
      )}
      <g filter={hand}>
        <g fill="none" stroke="var(--sumi)" strokeWidth="2.4" strokeLinecap="round">
          <circle cx="32" cy="23" r="9" />
          <path d="M18,48 c2,-10 7,-14 14,-14 c7,0 12,4 14,14" />
        </g>
        <g fill="none" stroke="var(--sumi-soft)" strokeWidth="1.5" strokeLinecap="round">
          <path d="M25,19 c3,-3 11,-3 14,0" />
          <path d="M28,25 h2 M34,25 h2" />
        </g>
      </g>
      <circle cx="47" cy="16" r="5.5" fill="var(--shu)" filter={wash} />
      <path
        d="M44.5,16 l2,2 3.5,-4"
        fill="none"
        stroke="var(--washi)"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </>
  ),

  // ── 提示詞：毛筆 + 筆下的一道朱線 ──
  prompts: (hand, wash, tint) => (
    <>
      {tint > 0 && (
        <path
          d="M25,44 c-4.5,3.5 -9,4.5 -12.5,3.5 c3.5,-2.5 4.5,-7 4.5,-10z"
          fill="var(--sumi)"
          opacity="0.75"
          filter={wash}
        />
      )}
      <g filter={hand}>
        <path
          d="M44,13 l8,8 -23,23 -10,2 2,-10z"
          fill="none"
          stroke="var(--sumi)"
          strokeWidth="2.4"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <path d="M40,17 l8,8" fill="none" stroke="var(--sumi-soft)" strokeWidth="1.5" strokeLinecap="round" />
      </g>
      <path
        d="M16,53 c9,2.5 20,1.5 29,-3.5"
        fill="none"
        stroke="var(--shu)"
        strokeWidth="2.6"
        strokeLinecap="round"
        filter={hand}
      />
      <circle cx="49" cy="10" r="2" fill="var(--kohaku)" />
    </>
  ),

  // ── 試拍：場記板 + 一顆紅燈 ──
  testshot: (hand, wash, tint) => (
    <>
      {tint > 0 && (
        <rect x="12" y="27" width="40" height="22" rx="2.5" fill="var(--fuji)" opacity="0.5" filter={wash} />
      )}
      <g filter={hand}>
        <g fill="none" stroke="var(--sumi)" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
          <rect x="11" y="26" width="41" height="23" rx="2.5" />
          <path d="M11,19 l38,-5.5 2.5,8.5 -38,5.5z" />
        </g>
        <g fill="none" stroke="var(--sumi)" strokeWidth="2" strokeLinecap="round">
          <path d="M22,14.5 l3,8 M33,13 l3,8 M44,11.5 l3,8" />
        </g>
      </g>
      <circle cx="44" cy="42" r="3.4" fill="var(--shu)" filter={wash} />
      <g fill="none" stroke="var(--sumi-soft)" strokeWidth="1.5" strokeLinecap="round">
        <path d="M18,34 h14 M18,40 h9" />
      </g>
    </>
  ),

  // ── 批次生成：四格，三格打勾一格還在排隊 ──
  generating: (hand, wash, tint) => (
    <>
      {tint > 0 && (
        <>
          <rect x="13" y="15" width="15" height="13" rx="2" fill="var(--kohaku)" opacity="0.45" filter={wash} />
          <rect x="32" y="15" width="15" height="13" rx="2" fill="var(--sakura)" opacity="0.6" filter={wash} />
          <rect x="13" y="33" width="15" height="13" rx="2" fill="var(--fuji)" opacity="0.55" filter={wash} />
        </>
      )}
      <g filter={hand} fill="none" stroke="var(--sumi)" strokeWidth="2.4" strokeLinejoin="round">
        <rect x="12" y="14" width="15" height="13" rx="2" />
        <rect x="32" y="14" width="15" height="13" rx="2" />
        <rect x="12" y="32" width="15" height="13" rx="2" />
        <rect
          x="32"
          y="32"
          width="15"
          height="13"
          rx="2"
          strokeDasharray="3.5 3.5"
          stroke="var(--sumi-faint)"
          strokeWidth="2"
        />
      </g>
      <g fill="none" stroke="var(--midori)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M17,21 l2,2 4,-4.5" />
        <path d="M37,21 l2,2 4,-4.5" />
        <path d="M17,39 l2,2 4,-4.5" />
      </g>
    </>
  ),

  // ── 接片：膠片，中間那格已經有畫面 ──
  assembled: (hand, wash, tint) => (
    <>
      {tint > 0 && (
        <rect x="11" y="21" width="42" height="21" rx="2" fill="var(--fuji)" opacity="0.45" filter={wash} />
      )}
      <g filter={hand}>
        <rect
          x="10"
          y="20"
          width="43"
          height="22"
          rx="2"
          fill="none"
          stroke="var(--sumi)"
          strokeWidth="2.4"
          strokeLinejoin="round"
        />
        <g fill="none" stroke="var(--sumi)" strokeWidth="1.8">
          <path d="M10,26.5 h43 M10,35.5 h43" />
          <path d="M24,20 v6.5 M38,20 v6.5 M24,35.5 v6.5 M38,35.5 v6.5" />
        </g>
      </g>
      {tint > 0 && (
        <rect x="25" y="27.5" width="12" height="7" fill="var(--kohaku)" opacity="0.55" filter={wash} />
      )}
      <path
        d="M17,48 c8,3 20,3 29,0"
        fill="none"
        stroke="var(--shu)"
        strokeWidth="2.2"
        strokeLinecap="round"
        filter={hand}
      />
    </>
  ),

  // ── 內容預審：放大鏡裡的驚嘆號 ──
  screening: (hand, wash, tint) => (
    <>
      {tint > 0 && <circle cx="28" cy="27" r="12.5" fill="var(--shu)" opacity="0.28" filter={wash} />}
      <g filter={hand}>
        <g fill="none" stroke="var(--sumi)" strokeWidth="2.4" strokeLinecap="round">
          <circle cx="28" cy="26" r="13" />
          <path d="M37.5,35.5 l10,11" />
        </g>
      </g>
      <path d="M28,19 v9" stroke="var(--shu)" strokeWidth="3" strokeLinecap="round" fill="none" />
      <circle cx="28" cy="33" r="1.9" fill="var(--shu)" />
      {tint > 0 && (
        <path
          d="M20,20 c2,-2.5 4.5,-4 7,-4.5"
          fill="none"
          stroke="var(--washi)"
          strokeWidth="2"
          strokeLinecap="round"
          opacity="0.9"
        />
      )}
      <path d="M45,44 l4,4.5" stroke="var(--kohaku)" strokeWidth="2.4" strokeLinecap="round" fill="none" />
    </>
  ),

  // ── 字幕：對話框 + 三語小點 ──
  subtitles: (hand, wash, tint) => (
    <>
      {tint > 0 && (
        <path
          d="M14,16 h36 a3,3 0 0 1 3,3 v18 a3,3 0 0 1 -3,3 h-20 l-9,7 v-7 h-7 a3,3 0 0 1 -3,-3 v-18 a3,3 0 0 1 3,-3z"
          fill="var(--kohaku)"
          opacity="0.4"
          filter={wash}
        />
      )}
      <g filter={hand}>
        <path
          d="M15,15 h36 a3,3 0 0 1 3,3 v18 a3,3 0 0 1 -3,3 h-20 l-9,7 v-7 h-7 a3,3 0 0 1 -3,-3 v-18 a3,3 0 0 1 3,-3z"
          fill="none"
          stroke="var(--sumi)"
          strokeWidth="2.4"
          strokeLinejoin="round"
        />
        <g fill="none" stroke="var(--sumi-soft)" strokeWidth="1.8" strokeLinecap="round">
          <path d="M22,24 h22 M22,31 h13" />
        </g>
      </g>
      <circle cx="41" cy="31" r="1.7" fill="var(--shu)" />
      <circle cx="46" cy="31" r="1.7" fill="var(--kohaku)" />
      <circle cx="51" cy="31" r="1.7" fill="var(--midori)" opacity="0.9" />
    </>
  ),

  // ── 學習迴路：迴圈 + 四個節點（通過／重拍／提煉／規劃）──
  learning: (hand, wash, tint) => (
    <>
      {tint > 0 && <circle cx="32" cy="32" r="15" fill="var(--fuji)" opacity="0.4" filter={wash} />}
      <g filter={hand}>
        <path
          d="M46,29 a15,15 0 1 0 -1.5,12"
          fill="none"
          stroke="var(--sumi)"
          strokeWidth="2.4"
          strokeLinecap="round"
        />
        <path
          d="M46.5,19.5 v9.5 h-9.5"
          fill="none"
          stroke="var(--sumi)"
          strokeWidth="2.4"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </g>
      <circle cx="32" cy="32" r="4" fill="var(--midori)" opacity="0.85" filter={wash} />
      <circle cx="20" cy="24" r="2.2" fill="var(--kohaku)" />
      <circle cx="24" cy="44" r="2.2" fill="var(--shu)" />
      <circle cx="43" cy="42" r="2.2" fill="var(--fuji)" />
    </>
  ),

  // ── 解析疑慮：三角警示 ──
  //    刻意用三角形而不是放大鏡：放大鏡是「內容預審」的形，
  //       兩個都在專案頁上，長太像使用者會分不出哪個是哪個。
  caution: (hand, wash, tint) => (
    <>
      {tint > 0 && (
        <path d="M32,14 l19,33 h-38z" fill="var(--kohaku)" opacity="0.45" filter={wash} />
      )}
      <g filter={hand}>
        <path
          d="M32,13 c1.4,0 2.6,0.8 3.3,2 l17,29.5 c1.4,2.4 -0.3,5.5 -3.3,5.5 h-34 c-3,0 -4.7,-3.1 -3.3,-5.5 l17,-29.5 c0.7,-1.2 1.9,-2 3.3,-2z"
          fill="none"
          stroke="var(--sumi)"
          strokeWidth="2.4"
          strokeLinejoin="round"
        />
      </g>
      <path d="M32,25 v11" stroke="var(--kohaku)" strokeWidth="3" strokeLinecap="round" fill="none" />
      <circle cx="32" cy="42" r="2" fill="var(--kohaku)" />
    </>
  ),

  // ── 分段：一疊章節，每段自己的長度 ──
  segments: (hand, wash, tint) => (
    <>
      {tint > 0 && (
        <>
          <rect x="13" y="15" width="34" height="9" rx="2" fill="var(--sakura)" opacity="0.55" filter={wash} />
          <rect x="13" y="28" width="24" height="9" rx="2" fill="var(--fuji)" opacity="0.5" filter={wash} />
          <rect x="13" y="41" width="30" height="9" rx="2" fill="var(--kohaku)" opacity="0.4" filter={wash} />
        </>
      )}
      <g filter={hand} fill="none" stroke="var(--sumi)" strokeWidth="2.4" strokeLinejoin="round">
        <rect x="12" y="14" width="34" height="10" rx="2" />
        <rect x="12" y="27" width="24" height="10" rx="2" />
        <rect x="12" y="40" width="30" height="10" rx="2" />
      </g>
      {/* 裝飾：右邊的章節記號 */}
      <g fill="none" stroke="var(--sumi-faint)" strokeWidth="1.8" strokeLinecap="round">
        <path d="M50,19 h3 M40,32 h3 M46,45 h3" />
      </g>
    </>
  ),

  // ── 退回：逆時針的回轉箭頭 ──
  rewind: (hand, wash, tint) => (
    <>
      {tint > 0 && <circle cx="32" cy="32" r="15" fill="var(--shu)" opacity="0.2" filter={wash} />}
      <g filter={hand}>
        <path
          d="M18,35 a15,15 0 1 1 2,10"
          fill="none"
          stroke="var(--sumi)"
          strokeWidth="2.4"
          strokeLinecap="round"
        />
        <path
          d="M17.5,25.5 v9.5 h9.5"
          fill="none"
          stroke="var(--sumi)"
          strokeWidth="2.4"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </g>
      {/* 裝飾：退回會作廢的東西 —— 用朱色點提醒那是有代價的動作 */}
      <circle cx="40" cy="26" r="2.4" fill="var(--shu)" opacity="0.85" />
      <circle cx="43" cy="36" r="1.8" fill="var(--shu)" opacity="0.55" />
    </>
  ),

  // ── 媒體庫：相片堆 ──
  media: (hand, wash, tint) => (
    <>
      {tint > 0 && (
        <rect x="13" y="17" width="38" height="28" rx="3" fill="var(--sakura)" opacity="0.5" filter={wash} />
      )}
      <g filter={hand}>
        <rect
          x="12"
          y="16"
          width="39"
          height="29"
          rx="3"
          fill="none"
          stroke="var(--sumi)"
          strokeWidth="2.4"
          strokeLinejoin="round"
        />
        <path
          d="M12,38 l10.5,-10 7,6.5 8.5,-8.5 13,12"
          fill="none"
          stroke="var(--sumi)"
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </g>
      <circle cx="23" cy="25" r="3.4" fill="var(--kohaku)" opacity="0.85" filter={wash} />
      <path
        d="M17,50 h33"
        stroke="var(--sumi-faint)"
        strokeWidth="2.2"
        strokeLinecap="round"
        fill="none"
        opacity="0.6"
      />
    </>
  ),
}

/**
 * Logo —— 朱印裡的草書 L。
 *
 * 設計：以單字 L 的大草書為主題，放進一枚朱印裡，顏色取最淺的 #C97887。
 *
 * 草書的粗細變化是分段筆畫疊接做的 —— SVG 一條 path 只能有一個
 * stroke-width，所以拆成四段（起筆藏鋒 → 豎畫行筆 → 底部頓挫 → 飛白挑出），
 * 用 round linecap 銜接就看不出接縫。飛白是拿印章色的細線在挑出那段刮出來的。
 *
 * 印章色不是 `--shu`。全站朱色為了文字對比度加深到
 *    #9A4553（正文原本低於 WCAG AA），Logo 是圖形不受那條限制，
 *    但也不能反過來把全站朱色一起調淺 —— 那些警告文字會變回讀不清楚。
 *    所以 Logo 有自己的 `--seal`。
 *
 * Args:
 *   size: 邊長 px
 *   plain: 拿掉水彩濾鏡。32px 以下一定要開，否則印章邊緣會被推爛。
 */
export function Logo({ size = 40, plain = false }: { size?: number; plain?: boolean }) {
  const wash = plain ? undefined : 'url(#gl-wash)'
  const hand = plain ? undefined : 'url(#gl-hand)'
  return (
    <svg viewBox="0 0 200 200" width={size} height={size} aria-label="Greenlight" role="img">
      <rect x="26" y="26" width="148" height="148" rx="14" fill="var(--seal)" filter={wash} />
      <g filter={hand}>
        <ellipse cx="72" cy="56" rx="11" ry="8.5" transform="rotate(-24 72 56)" fill="var(--washi)" />
        <g fill="none" stroke="var(--washi)" strokeLinecap="round" strokeLinejoin="round">
          <path d="M70,56 C67,84 65,112 67,134" strokeWidth="17" />
          <path d="M67,134 C68,149 79,157 96,154" strokeWidth="15" />
          <path d="M96,154 C114,151 130,146 144,139" strokeWidth="11.5" />
          <path d="M144,139 C150,136 155,133 160,130" strokeWidth="6" />
        </g>
        {/* 飛白 —— 小尺寸會糊成髒點，所以跟濾鏡一起關掉 */}
        {!plain && (
          <g stroke="var(--seal)" strokeLinecap="round" fill="none" opacity="0.75">
            <path d="M112,149 C124,146 136,142 146,138" strokeWidth="1.4" />
            <path d="M122,152 C132,149 142,145 151,141" strokeWidth="1" />
          </g>
        )}
      </g>
    </svg>
  )
}
