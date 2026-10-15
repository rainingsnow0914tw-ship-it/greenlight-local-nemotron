/**
 * 介面文案 —— 三個語言各自一份，介面右上角切換。
 *
 * 這一份是純翻譯，沒有邏輯。跟後端的 config/prompts/{zh,en,ja}.yaml
 * 是同樣的精神：語言差異獨立成房間，邏輯只有一份。
 *
 * 為什麼前端自己放一份而不是跟後端拿：
 *   介面文字要在**還沒連上後端**的時候就能顯示（載入中、連線失敗畫面）。
 *   跟後端要的話，連不上時整個介面會是空白，那時候最需要文字告訴使用者發生什麼事。
 */

import type { Lang } from './api'

export interface Copy {
  brand: string
  tagline: string

  // 上傳
  dropTitle: string
  dropHint: string
  dropFormats: string
  dropButton: string
  reading: string

  // 上傳後的確認
  reviewTitle: string
  fileInfo: string
  chaptersFound: string
  noChapters: string
  parseCost: string
  parseCostNote: string
  parseButton: string
  parseWaiting: string
  cancel: string

  // 專案
  projectsTitle: string
  duplicateProject: string
  deleteProject: string
  deleteConfirm: string
  deleteFiles: string
  deleteSpent: string
  deleteKeeps: string
  noProjects: string
  characters: string
  principal: string
  supporting: string
  nonHuman: string
  noDescription: string
  scenes: string
  segments: string
  spent: string
  ofCeiling: string

  // 表格欄位
  colScene: string
  colLocation: string
  colTime: string
  colSeconds: string
  colWho: string
  colSummary: string
  colSource: string
  colLines: string
  colAppearance: string

  colNo: string
  colChapter: string
  colLength: string
  colCost: string
  colStatus: string

  // 段落頁
  lookTitle: string
  lookLocked: string
  planShots: string
  planShotsNote: string
  planShotsButton: string
  planning: string
  variants: string
  colTier: string
  colListPrice: string
  colRetry: string
  colTotal: string
  successRate: string
  recommended: string
  tierChosen: string
  tierHint: string
  overBudget: string
  retryNote: string
  riskShots: string
  testShot: string
  shotList: string
  shots: string
  veoCalls: string
  colShotId: string
  colFraming: string
  colMove: string
  colLink: string
  noPrompt: string
  colGuards: string
  guardsApplied: string
  guardsSkipped: string
  /** 帶兩個佔位：{matched} 匹配到幾條、{applied} 套用得下幾條 */
  guardsCapped: string
  guardsAdvisory: string

  // 選角關卡
  castingTitle: string
  castingWhy: string
  castingProgress: string
  castingBlocked: string
  castingReady: string
  castingRoundCost: string
  castingGenerate: string
  castingGenerating: string
  castingRerollTitle: string
  castingNoCandidates: string
  castingSelect: string
  castingSelected: string
  castingUnlock: string
  castingLockedNote: string
  // ── 身份參考（臉）與段落造型 ──
  identityTitle: string
  identityUploaded: string
  identityGenerated: string
  identityNone: string
  identityUpload: string
  identityReplace: string
  identityClear: string
  identityHint: string
  identityConsent: string
  identityConsentRequired: string
  identityUploading: string
  identityGenerate: string
  identityGenerateUnlockNote: string
  lookModeTitle: string
  lookModeDefault: string
  lookModeSegment: string
  lookModeHint: string
  lookGenerate: string
  lookUse: string
  lookInUse: string
  lookReset: string
  lookNeedLock: string
  castingFromScript: string
  castingInferred: string
  castingRound: string
  castingImageFailed: string
  castingTextOnly: string
  castingOpenButton: string
  castingShowAll: string
  castingCropNote: string
  castingHideOld: string

  // 提示詞導出
  promptsTitle: string
  promptsNote: string
  promptsCopy: string
  promptsCopied: string
  promptsNarration: string
  segFormatTitle: string
  segFormatInherit: string
  segFormatInheritHint: string
  segStyleTitle: string
  segFormatReplan: string
  segFormatReplanButton: string
  segFormatReplanCost: string
  promptsOffscreen: string
  promptsDownload: string
  promptsShowFull: string
  promptsHideFull: string
  promptsConstraints: string
  promptsNotReady: string
  // 試拍 / 批次 / 接片
  testShotTitle: string
  testShotWhy: string
  testShotRun: string
  testShotRunning: string
  testShotApprove: string
  testShotApproved: string
  testShotRetake: string
  batchTitle: string
  batchWhy: string
  batchProgress: string
  batchNext: string
  batchGenerating: string
  batchApprove: string
  batchRetry: string
  batchRetake: string
  batchRetryMute: string
  batchRetryMuteHint: string
  rejectWhy: string
  errorBlocked: string
  rejectSuggestion: string
  batchRetakeHint: string
  takesLabel: string
  takesCurrent: string
  takesUse: string
  takesSwitched: string
  batchReset: string
  batchResetHint: string
  batchWaiting: string
  batchDone: string
  batchDoneNext: string
  batchDoneHint: string
  batchRemaining: string
  batchAttempts: string
  assembleTitle: string
  assembleWhy: string
  assembleRun: string
  assembling: string
  assembleDownload: string
  assembleSubtitles: string
  assembleSubtitleHint: string

  promptsLanguage: string
  promptsLanguageNote: string
  promptsRewriting: string
  promptsCurrentLanguage: string

  // 其他
  warnings: string
  back: string
  viewSource: string
  hideSource: string
  minutes: string
  seconds: string
  scenesUnit: string
  loading: string
  errorTitle: string
  retry: string
}

const zh: Copy = {
  brand: 'Greenlight Local',
  tagline: 'NVIDIA Nemotron 專用版・從劇本，到畫面',
  dropTitle: '把劇本丟進來',
  dropHint: '拖放檔案，或點擊選擇',
  dropFormats: '支援 txt · md · docx · pdf · fountain',
  dropButton: '選擇檔案',
  reading: '讀取中……',
  reviewTitle: '確認一下',
  fileInfo: '檔案',
  chaptersFound: '偵測到章節',
  noChapters: '沒有偵測到章節標記，解析後可以請系統建議分段',
  parseCost: '解析費用',
  parseCostNote: '解析是第一個花錢的步驟。按下去才會扣款。',
  parseButton: '開始解析',
  parseWaiting: '解析中，約需 30–90 秒……',
  cancel: '取消',
  projectsTitle: '進行中的作品',
  duplicateProject: '開新版本',
  deleteProject: '刪除',
  deleteConfirm: '確定刪掉這個專案？刪了救不回來。',
  deleteFiles: '會刪掉的圖檔與影片檔',
  deleteSpent: '這個專案已花費',
  deleteKeeps: '系統學到的生成紀錄會保留（跨專案累積，不佔空間）。',
  noProjects: '還沒有作品',
  characters: '角色',
  principal: '主角',
  supporting: '配角',
  nonHuman: '非人',
  noDescription: '劇本未寫外貌',
  scenes: '場次',
  segments: '分段',
  spent: '已花費',
  ofCeiling: '預算上限',
  colScene: '場',
  colLocation: '地點',
  colTime: '時間',
  colSeconds: '秒',
  colWho: '在場',
  colSummary: '劇情',
  colSource: '原文',
  colLines: '台詞',
  colAppearance: '外貌',
  colNo: '#',
  colChapter: '章節',
  colLength: '長度',
  colCost: '概估',
  colStatus: '狀態',
  lookTitle: '定調',
  lookLocked: '已分鏡，改風格要重新分鏡',
  planShots: '分鏡',
  planShotsNote: '分鏡會把這一段拆成一個個可生成的鏡頭，並判斷哪些要接首尾幀、哪些需要續拍。按下去才會扣款。',
  planShotsButton: '開始分鏡',
  planning: '分鏡中，約需 30–120 秒……',
  variants: '方案比較',
  colTier: '檔位',
  colListPrice: '標價',
  colRetry: '重拍',
  colTotal: '總計',
  successRate: '一次過關',
  recommended: '建議',
  tierChosen: '已選用',
  tierHint: '點一列選定畫質檔位。還沒選的話，生成會用最便宜的檔位。',
  overBudget: '超預算',
  retryNote: '「重拍」那一欄常常比「標價」還貴 —— 便宜檔位的重拍次數多，算下來未必真的省。這是自己去官網按一按看不到的數字。',
  riskShots: '風險鏡頭',
  testShot: '建議先試拍',
  shotList: '鏡頭表',
  shots: '鏡頭',
  veoCalls: '生成次數',
  colShotId: '鏡號',
  colFraming: '鏡別',
  colMove: '運鏡',
  colLink: '接續',
  noPrompt: '（尚未產生提示詞）',
  colGuards: '約束',
  guardsApplied: '這一鏡送出去的物理約束',
  guardsSkipped: '講不下的約束',
  guardsCapped: '這一鏡匹配到 {matched} 條約束，但一次只講得下 {applied} 條',
  guardsAdvisory: '提示詞解決不了、要改工作流的事',
  castingTitle: '選角',
  castingWhy:
    '角色長相是所有鏡頭的地基。一張候選圖 $0.02、一個八秒鏡頭 $0.80、一支三分鐘的片 $18 —— 在這裡多挑幾輪，比之後重來便宜得多。',
  castingProgress: '已選定',
  castingBlocked: '所有角色選定後才能開始生成影片',
  castingReady: '角色都選定了，可以往下走',
  castingRoundCost: '每輪三張',
  castingGenerate: '生成候選',
  castingGenerating: '生成中，約需 20–30 秒。遇到流量限制會自動等待重試（不會重複扣款），這時可能要一兩分鐘。',
  castingRerollTitle: '換個方向再抽',
  castingNoCandidates: '還沒生成候選圖',
  castingSelect: '選這個',
  castingSelected: '已選定',
  castingUnlock: '解除選定',
  castingLockedNote: '已鎖定。要重抽必須先解除選定。',
  identityTitle: '身份參考（臉）',
  identityUploaded: '你上傳的',
  identityGenerated: '取自選定的候選',
  identityNone: '還沒有。第一次選定候選時會自動取用；也可以上傳一張照片。',
  identityUpload: '上傳照片當這個角色的臉',
  identityReplace: '換一張',
  identityClear: '移除',
  identityHint: '上傳的照片不是角色卡：設計師會拿這張臉、照本片風格重新生造型。適合自己當主角，或自帶角色設定圖。',
  identityConsent: '我確認我有權使用這張圖片；因肖像、版權或人格權產生的任何責任由我承擔，與本服務無關。',
  identityConsentRequired: '要先勾選確認才能上傳。',
  identityUploading: '上傳中…',
  identityGenerate: '用這張臉生成角色卡',
  identityGenerateUnlockNote: '這個角色已鎖定 —— 會先解除選定，再用這張臉重新生三張造型候選。已生成的鏡頭不會自動失效。',
  lookModeTitle: '造型用於',
  lookModeDefault: '全片預設',
  lookModeSegment: '第 {n} 段',
  lookModeHint: '換衣服、換髮型是段落層的事，臉全片鎖死。選一段，用同一張臉生這一段的造型。',
  lookGenerate: '為第 {n} 段生造型',
  lookUse: '用在第 {n} 段',
  lookInUse: '第 {n} 段使用中',
  lookReset: '第 {n} 段回到預設造型',
  lookNeedLock: '先選定角色本體，才能為各段生造型。',
  castingFromScript: '依劇本描述',
  castingInferred: '系統推測',
  castingRound: '第 {n} 輪',
  castingImageFailed: '這張沒生出來',
  castingTextOnly: '文字版（還沒有生圖模型）',
  castingOpenButton: '選角',
  castingShowAll: '看全部 {n} 張候選',
  castingCropNote: '候選圖的實際檔案是 16:9 橫式（跟影片一致），卡片只是直式裁切預覽。',
  castingHideOld: '只看最新一輪',
  promptsTitle: '提示詞',
  promptsNote: '這裡是這一段每個鏡頭的完整提示詞，你可以直接拿去別的工具用。這一步不花錢 —— 在這裡發現描述不對，比生成完才發現便宜九百倍。',
  promptsCopy: '複製',
  promptsCopied: '已複製',
  promptsNarration: '旁白 · TTS 配音',
  segFormatTitle: '這一段用什麼方式講',
  segFormatInherit: '跟專案設定',
  segFormatInheritHint: '用整部片的設定',
  segStyleTitle: '這一段看起來像什麼',
  segFormatReplan: '已經分過鏡了 —— 要對這一段重新分鏡，新的敘事策略才會生效（約 $0.02–0.05）。',
  segFormatReplanButton: '用新的敘事形式重新分鏡',
  segFormatReplanCost: '約 $0.02–0.05。⚠️ 會作廢這一段的試拍與批次進度（影片檔留在媒體庫）。',
  promptsOffscreen: '畫外音',
  promptsDownload: '下載',
  promptsShowFull: '看完整提示詞',
  promptsHideFull: '收起',
  promptsConstraints: '套用的物理約束',
  promptsNotReady: '這一段還沒分鏡，沒有提示詞可以導出。',
  testShotTitle: '試拍一鏡',
  testShotWhy: '先生一鏡，換一個「這批到底能不能看」的答案。整段要生好幾個小時，這一鏡幾分鐘。試拍不通過就不能開始批次生成。',
  testShotRun: '試拍',
  testShotRunning: '生成中，約需 1–3 分鐘……',
  testShotApprove: '通過，開始批次',
  testShotApproved: '試拍已通過',
  testShotRetake: '重新試拍',
  batchTitle: '批次生成',
  batchWhy: '一鏡一鏡生，每一鏡看過再生下一鏡。中途離開也沒關係 —— 進度存著，回來從同一個地方接下去。',
  batchProgress: '已通過',
  batchNext: '生成下一鏡',
  batchGenerating: '生成中，約需 1–3 分鐘……',
  batchApprove: '通過',
  batchRetry: '重拍',
  batchRetake: '反悔重拍',
  batchRetryMute: '無聲重拍（字幕補台詞）',
  batchRetryMuteHint: '這句台詞被內容審查擋下。無聲重拍會照常生成畫面，台詞改由字幕呈現——劇本原文不變。',
  rejectWhy: '最可能是這一段沒過',
  errorBlocked: '這一鏡被 Google 的內容安全審查擋下來了，影片沒有生成（這次不計費）。重試同樣的內容會再被擋。',
  rejectSuggestion: '可以怎麼調整',
  batchRetakeHint: '重拍後回到待確認；接好的段落成片會過期，重接一次就好（$0）。',
  takesLabel: '歷次拍攝',
  takesCurrent: '採用中',
  takesUse: '用這次',
  takesSwitched: '每一次拍的都留著（$0 切換）——重拍不保證更好，用哪次由你決定。',
  batchReset: '整段重來',
  batchResetHint: '清掉所有鏡頭進度並解除角色音色鎖定。已生成的影片檔會留著。',
  batchWaiting: '這一鏡在等你確認',
  batchDone: '這一段全部生完了',
  batchDoneNext: '接成一支完整影片',
  batchDoneHint: '22 鏡都通過了。下一步是把它們接起來、加字幕。',
  batchRemaining: '剩餘預估',
  batchAttempts: '第 {n} 次',
  assembleTitle: '接片與字幕',
  assembleWhy: '把生成好的鏡頭接成一支完整影片。字幕是外掛的 —— 影片只生一次，字幕想出幾種語言都行。',
  assembleRun: '接成完整影片',
  assembling: '接片中……',
  assembleDownload: '下載影片',
  assembleSubtitles: '字幕',
  assembleSubtitleHint: '下載 .srt 後跟影片放同一個資料夾、檔名取一樣，播放器就會自動載入。',
  promptsLanguage: '提示詞語言',
  promptsLanguageNote:
    '換語言只改「畫面描述」，台詞不動 —— 台詞是角色真的要說的話，翻譯它等於改掉影片內容。拿去別的工具測試時，英文版通常最穩。',
  promptsRewriting: '改寫中……',
  promptsCurrentLanguage: '目前',
  warnings: '解析時的疑慮',
  back: '← 返回',
  viewSource: '看原文',
  hideSource: '收起',
  minutes: '分',
  seconds: '秒',
  scenesUnit: '場',
  loading: '載入中……',
  errorTitle: '出了點問題',
  retry: '重試',
}

const en: Copy = {
  brand: 'Greenlight Local',
  tagline: 'NVIDIA Nemotron Edition · From script to screen',
  dropTitle: 'Drop your script here',
  dropHint: 'Drag a file in, or click to choose',
  dropFormats: 'txt · md · docx · pdf · fountain',
  dropButton: 'Choose file',
  reading: 'Reading…',
  reviewTitle: 'Before we start',
  fileInfo: 'File',
  chaptersFound: 'Chapters found',
  noChapters: 'No chapter markers found — we can suggest a breakdown after parsing',
  parseCost: 'Parsing cost',
  parseCostNote: 'Parsing is the first step that costs money. Nothing is charged until you press this.',
  parseButton: 'Parse script',
  parseWaiting: 'Parsing — this takes 30–90 seconds…',
  cancel: 'Cancel',
  projectsTitle: 'Works in progress',
  duplicateProject: 'New version',
  deleteProject: 'Delete',
  deleteConfirm: 'Delete this project? This cannot be undone.',
  deleteFiles: 'Images and videos to be deleted',
  deleteSpent: 'Spent on this project',
  deleteKeeps: 'Learning records are kept — they accumulate across projects and take no space.',
  noProjects: 'Nothing here yet',
  characters: 'Characters',
  principal: 'Lead',
  supporting: 'Supporting',
  nonHuman: 'Non-human',
  noDescription: 'No appearance in script',
  scenes: 'Scenes',
  segments: 'Segments',
  spent: 'Spent',
  ofCeiling: 'of ceiling',
  colScene: 'Sc.',
  colLocation: 'Location',
  colTime: 'Time',
  colSeconds: 'Sec',
  colWho: 'Present',
  colSummary: 'Action',
  colSource: 'Source',
  colLines: 'Lines',
  colAppearance: 'Appearance',
  colNo: '#',
  colChapter: 'Chapter',
  colLength: 'Length',
  colCost: 'Est.',
  colStatus: 'Status',
  lookTitle: 'Look',
  lookLocked: 'Already storyboarded — changing style means re-planning',
  planShots: 'Storyboard',
  planShotsNote: 'This breaks the segment into individual generatable shots, and decides which ones link frames and which need extending. Nothing is charged until you press this.',
  planShotsButton: 'Break into shots',
  planning: 'Working — this takes 30–120 seconds…',
  variants: 'Compare tiers',
  colTier: 'Tier',
  colListPrice: 'List',
  colRetry: 'Retries',
  colTotal: 'Total',
  successRate: 'First-pass',
  recommended: 'Recommended',
  tierChosen: 'Selected',
  tierHint: 'Click a row to choose the quality tier. Until you choose, generation uses the cheapest tier.',
  overBudget: 'Over budget',
  retryNote: 'The retry column is often larger than the list price — cheaper tiers fail more often, so they may not actually be cheaper. This is the number you cannot see on a vendor pricing page.',
  riskShots: 'Shots at risk',
  testShot: 'Test shot',
  shotList: 'Shot list',
  shots: 'Shots',
  veoCalls: 'Renders',
  colShotId: 'Shot',
  colFraming: 'Size',
  colMove: 'Move',
  colLink: 'Link',
  noPrompt: '(prompt not generated yet)',
  colGuards: 'Guards',
  guardsApplied: 'Physical guards sent with this shot',
  guardsSkipped: "Guards that didn't fit",
  guardsCapped: 'This shot matched {matched} guards but only {applied} fit in one prompt',
  guardsAdvisory: 'Needs a workflow change — a prompt cannot fix these',
  castingTitle: 'Casting',
  castingWhy:
    'How your characters look is the foundation every shot rests on. A candidate image takes seconds, an eight-second shot several minutes, a whole film hours — a few extra rounds here are far cheaper than redoing it later.',
  castingProgress: 'locked in',
  castingBlocked: 'Every character must be locked in before generation starts',
  castingReady: 'All characters locked in — ready to move on',
  castingRoundCost: 'three per round',
  castingGenerate: 'Generate candidates',
  castingGenerating: 'Generating, about 20–30 seconds. If we hit a rate limit it retries automatically (no double charge) — that can take a minute or two.',
  castingRerollTitle: 'Try another direction',
  castingNoCandidates: 'No candidates yet',
  castingSelect: 'Choose this',
  castingSelected: 'Locked in',
  castingUnlock: 'Unlock',
  castingLockedNote: 'Locked. Unlock first to generate more candidates.',
  identityTitle: 'Identity reference (the face)',
  identityUploaded: 'uploaded by you',
  identityGenerated: 'taken from the selected candidate',
  identityNone: 'None yet. Set automatically when you first select a candidate — or upload a photo.',
  identityUpload: "Upload a photo as this character's face",
  identityReplace: 'Replace',
  identityClear: 'Remove',
  identityHint: "An uploaded photo is not the character card: the designer takes this face and re-renders the look in this film's style. For starring yourself, or bringing your own character sheet.",
  identityConsent: "I confirm I have the right to use this image; any liability arising from likeness, copyright or personality rights is mine, not this service's.",
  identityConsentRequired: 'Tick the confirmation before uploading.',
  identityUploading: 'Uploading…',
  identityGenerate: 'Generate character card from this face',
  identityGenerateUnlockNote: 'This character is locked — it will be unlocked first, then three new looks are generated from this face. Shots already generated are not invalidated automatically.',
  lookModeTitle: 'Look applies to',
  lookModeDefault: 'Whole film (default)',
  lookModeSegment: 'Segment {n}',
  lookModeHint: 'Wardrobe and hair belong to a segment; the face is locked for the whole film. Pick a segment and generate its look from the same face.',
  lookGenerate: 'Generate look for segment {n}',
  lookUse: 'Use in segment {n}',
  lookInUse: 'In use for segment {n}',
  lookReset: 'Segment {n}: back to default look',
  lookNeedLock: 'Select the character first, then generate per-segment looks.',
  castingFromScript: 'From the script',
  castingInferred: 'Inferred',
  castingRound: 'Round {n}',
  castingImageFailed: 'This one failed to generate',
  castingTextOnly: 'Text version (no image model yet)',
  castingOpenButton: 'Casting',
  castingShowAll: 'Show all {n} candidates',
  castingCropNote: 'The actual files are 16:9 landscape (same as the film) — cards just show a portrait crop.',
  castingHideOld: 'Latest round only',
  promptsTitle: 'Prompts',
  promptsNote: 'Every prompt for every shot in this segment — take them anywhere you like. This step is free, and catching a wrong description here costs 900× less than catching it after generation.',
  promptsCopy: 'Copy',
  promptsCopied: 'Copied',
  promptsNarration: 'Narration · TTS voice',
  segFormatTitle: 'How this segment is told',
  segFormatInherit: 'Follow project',
  segFormatInheritHint: 'Use the project-wide setting',
  segStyleTitle: 'How this segment looks',
  segFormatReplan: 'This segment is already broken into shots — re-plan it to apply the new narrative strategy (about $0.02–0.05).',
  segFormatReplanButton: 'Re-plan with the new format',
  segFormatReplanCost: 'About $0.02–0.05. ⚠️ Clears the test shot and batch progress for this segment (video files stay in the media library).',
  promptsOffscreen: 'Off camera',
  promptsDownload: 'Download',
  promptsShowFull: 'Show full prompt',
  promptsHideFull: 'Hide',
  promptsConstraints: 'Physical constraints applied',
  promptsNotReady: 'This segment has no shot list yet, so there are no prompts to export.',
  testShotTitle: 'Test shot',
  testShotWhy: 'One shot buys you the answer to "is this batch worth it". The full segment takes hours of GPU time; this takes minutes. Batch generation stays locked until a test shot passes.',
  testShotRun: 'Run test shot',
  testShotRunning: 'Generating — 1–3 minutes…',
  testShotApprove: 'Approve and unlock batch',
  testShotApproved: 'Test shot approved',
  testShotRetake: 'Shoot again',
  batchTitle: 'Batch generation',
  batchWhy: 'One shot at a time — you see each one before the next is generated. Leave whenever you like; progress is saved and picks up where you left off.',
  batchProgress: 'approved',
  batchNext: 'Generate next shot',
  batchGenerating: 'Generating — 1–3 minutes…',
  batchApprove: 'Approve',
  batchRetry: 'Reshoot',
  batchRetake: 'Retake this shot',
  batchRetryMute: 'Retake muted (line via subtitles)',
  batchRetryMuteHint: 'This line was blocked by content moderation. A muted retake generates the visuals; the line is delivered via subtitles — your script stays untouched.',
  rejectWhy: 'Most likely this passage',
  errorBlocked: 'Google’s content safety filter rejected this shot, so no video was generated (you were not charged). Retrying the same content will be rejected again.',
  rejectSuggestion: 'What you could change',
  batchRetakeHint: 'Goes back to review after the retake; the assembled segment becomes stale — just re-assemble ($0).',
  takesLabel: 'Takes',
  takesCurrent: 'in use',
  takesUse: 'Use this take',
  takesSwitched: 'Every take is kept — switching is $0. A retake is not always better; the director decides.',
  batchReset: 'Start the segment over',
  batchResetHint: 'Clears all shot progress and unlocks character voices. Generated files are kept.',
  batchWaiting: 'waiting for your review',
  batchDone: 'Every shot in this segment is done',
  batchDoneNext: 'Assemble into one film',
  batchDoneHint: 'All shots approved. Next: stitch them together and add subtitles.',
  batchRemaining: 'Remaining',
  batchAttempts: 'take {n}',
  assembleTitle: 'Assemble and subtitles',
  assembleWhy: 'Join the generated shots into one film. Subtitles ride alongside — the video is generated once and can carry as many languages as you like.',
  assembleRun: 'Assemble the film',
  assembling: 'Assembling…',
  assembleDownload: 'Download film',
  assembleSubtitles: 'Subtitles',
  assembleSubtitleHint: 'Put the .srt next to the video with a matching filename and most players will pick it up automatically.',
  promptsLanguage: 'Prompt language',
  promptsLanguageNote:
    'Switching language rewrites the visual descriptions only — dialogue stays as written, because translating it would change what the characters actually say. English tends to travel best to other tools.',
  promptsRewriting: 'Rewriting…',
  promptsCurrentLanguage: 'current',
  warnings: 'Parsing caveats',
  back: '← Back',
  viewSource: 'View source',
  hideSource: 'Hide',
  minutes: 'min',
  seconds: 's',
  scenesUnit: 'sc.',
  loading: 'Loading…',
  errorTitle: 'Something went wrong',
  retry: 'Retry',
}

const ja: Copy = {
  brand: 'Greenlight Local',
  tagline: 'NVIDIA Nemotron 専用版・脚本から、映像へ',
  dropTitle: '脚本をここに',
  dropHint: 'ファイルをドラッグ、またはクリックして選択',
  dropFormats: 'txt · md · docx · pdf · fountain に対応',
  dropButton: 'ファイルを選ぶ',
  reading: '読み込み中……',
  reviewTitle: '確認',
  fileInfo: 'ファイル',
  chaptersFound: '検出された章',
  noChapters: '章の区切りが見つかりません。解析後に分割案を提案できます',
  parseCost: '解析費用',
  parseCostNote: '解析はこの流れで最初に費用が発生する工程です。押すまで課金されません。',
  parseButton: '解析を始める',
  parseWaiting: '解析中、30〜90秒ほどかかります……',
  cancel: 'やめる',
  projectsTitle: '進行中の作品',
  duplicateProject: '新バージョン',
  deleteProject: '削除',
  deleteConfirm: 'このプロジェクトを削除しますか？取り消せません。',
  deleteFiles: '削除される画像と映像',
  deleteSpent: 'このプロジェクトの費用',
  deleteKeeps: '学習記録は保持されます（プロジェクト横断で蓄積、容量は取りません）。',
  noProjects: 'まだ作品がありません',
  characters: '登場人物',
  principal: '主要',
  supporting: '脇役',
  nonHuman: '非人間',
  noDescription: '容姿の記述なし',
  scenes: 'シーン',
  segments: '分割',
  spent: '使用額',
  ofCeiling: '上限',
  colScene: 'S',
  colLocation: '場所',
  colTime: '時間',
  colSeconds: '秒',
  colWho: '登場',
  colSummary: '内容',
  colSource: '原文',
  colLines: '台詞',
  colAppearance: '外貌',
  colNo: '#',
  colChapter: '章',
  colLength: '長さ',
  colCost: '概算',
  colStatus: '状態',
  lookTitle: '画づくり',
  lookLocked: 'コンテ済み。画風を変えるとやり直しになります',
  planShots: '絵コンテ',
  planShotsNote: 'この段落を一カットずつ生成可能な形に分解し、繋ぎと継ぎ足しの要否を判断します。押すまで課金されません。',
  planShotsButton: 'コンテを切る',
  planning: '作業中、30〜120秒ほどかかります……',
  variants: '画質の比較',
  colTier: '画質',
  colListPrice: '表示価格',
  colRetry: '撮り直し',
  colTotal: '合計',
  successRate: '一発OK率',
  recommended: 'おすすめ',
  tierChosen: '選択中',
  tierHint: '行をクリックして画質を選んでください。未選択の場合は最安の画質で生成されます。',
  overBudget: '予算超過',
  retryNote: '「撮り直し」は「表示価格」より大きくなることが多い —— 安い画質ほど失敗が増えるので、結局は安くならないことがあります。公式の価格表では見えない数字です。',
  riskShots: '注意が必要なカット',
  testShot: 'テスト撮影',
  shotList: 'カット表',
  shots: 'カット',
  veoCalls: '生成回数',
  colShotId: 'カット',
  colFraming: 'サイズ',
  colMove: 'カメラ',
  colLink: '繋ぎ',
  noPrompt: '（プロンプト未生成）',
  colGuards: '制約',
  guardsApplied: 'このカットに付けた物理制約',
  guardsSkipped: '入りきらなかった制約',
  guardsCapped: 'このカットは制約 {matched} 件に該当しますが、一度に入るのは {applied} 件です',
  guardsAdvisory: 'プロンプトでは直らない — 進め方を変える必要があります',
  castingTitle: 'キャスティング',
  castingWhy:
    'キャラクターの見た目は全カットの土台。候補画像 1 枚 $0.02、8 秒のカット $0.80、3 分の作品 $18 —— ここで何回か引き直すほうが、あとでやり直すよりずっと安い。',
  castingProgress: '確定済み',
  castingBlocked: '全キャラクターが確定するまで映像生成には進めません',
  castingReady: 'キャラクターが揃いました。次へ進めます',
  castingRoundCost: '1 回 3 枚',
  castingGenerate: '候補を生成',
  castingGenerating: '生成中、20〜30 秒ほど。レート制限に当たると自動で待って再試行します（二重課金なし）。その場合は1〜2分かかります。',
  castingRerollTitle: '方向を変えて引き直す',
  castingNoCandidates: 'まだ候補がありません',
  castingSelect: 'これにする',
  castingSelected: '確定済み',
  castingUnlock: '確定を解除',
  castingLockedNote: '確定済み。引き直すには先に解除してください。',
  identityTitle: '本人参照（顔）',
  identityUploaded: 'あなたがアップロード',
  identityGenerated: '確定した候補から取得',
  identityNone: 'まだありません。最初に候補を確定すると自動で設定されます。写真のアップロードも可能です。',
  identityUpload: '写真をこのキャラクターの顔として使う',
  identityReplace: '差し替え',
  identityClear: '削除',
  identityHint: 'アップロードした写真はキャラクターカードではありません。デザイナーがこの顔を使い、本作の画風で衣装・髪型を作り直します。自分が主役になる場合や、既存の設定画を持ち込む場合に。',
  identityConsent: 'この画像を使用する権利があることを確認します。肖像権・著作権・人格権に関する一切の責任は私にあり、本サービスは負いません。',
  identityConsentRequired: 'アップロード前に確認にチェックしてください。',
  identityUploading: 'アップロード中…',
  identityGenerate: 'この顔でキャラクターカードを生成',
  identityGenerateUnlockNote: 'このキャラクターは確定済みです。先に確定を解除し、この顔から新しいルック候補を 3 枚生成します。生成済みのショットは自動では無効になりません。',
  lookModeTitle: 'ルックの適用先',
  lookModeDefault: '全編（デフォルト）',
  lookModeSegment: '第 {n} 章',
  lookModeHint: '衣装と髪型は章ごと、顔は全編で固定。章を選び、同じ顔からその章のルックを生成します。',
  lookGenerate: '第 {n} 章のルックを生成',
  lookUse: '第 {n} 章で使う',
  lookInUse: '第 {n} 章で使用中',
  lookReset: '第 {n} 章をデフォルトに戻す',
  lookNeedLock: '先にキャラクター本体を確定してから、章ごとのルックを生成してください。',
  castingFromScript: '脚本の記述どおり',
  castingInferred: 'システムの推測',
  castingRound: '第 {n} 回',
  castingImageFailed: 'この 1 枚は生成に失敗しました',
  castingTextOnly: 'テキスト版（画像モデル未設定）',
  castingOpenButton: 'キャスティング',
  castingShowAll: '全 {n} 枚を見る',
  castingCropNote: '画像の実ファイルは映像と同じ 16:9 横位置です。カードは縦のプレビュー切り抜きです。',
  castingHideOld: '最新の回だけ表示',
  promptsTitle: 'プロンプト',
  promptsNote: 'このセグメントの全カット分のプロンプト。そのまま他のツールに持って行けます。この工程は無料 —— 記述の間違いをここで見つければ、生成後に気づくより 900 分の 1 のコストで済みます。',
  promptsCopy: 'コピー',
  promptsCopied: 'コピーしました',
  promptsNarration: 'ナレーション · TTS',
  segFormatTitle: 'この段落の語り口',
  segFormatInherit: 'プロジェクトに従う',
  segFormatInheritHint: '作品全体の設定を使う',
  segStyleTitle: 'この段落の見た目',
  segFormatReplan: 'この段落は既に絵コンテ化されています。新しい方針を適用するには再分割してください（約 $0.02–0.05）。',
  segFormatReplanButton: '新しい語り口で絵コンテを作り直す',
  segFormatReplanCost: '約 $0.02–0.05。⚠️ この段落のテスト撮影とバッチ進捗が破棄されます（動画ファイルはメディアライブラリに残ります）。',
  promptsOffscreen: '画面外の声',
  promptsDownload: 'ダウンロード',
  promptsShowFull: 'プロンプト全文',
  promptsHideFull: '閉じる',
  promptsConstraints: '適用された物理制約',
  promptsNotReady: 'このセグメントはまだ絵コンテがないため、書き出せるプロンプトがありません。',
  testShotTitle: 'テスト撮影',
  testShotWhy: '1 カット分の費用で「このバッチは見られるものになるか」の答えが買える。セグメント全体は $9〜$72、これは 1 ドル未満。テストが通るまでバッチ生成はロックされたまま。',
  testShotRun: 'テスト撮影',
  testShotRunning: '生成中、1〜3 分ほど……',
  testShotApprove: '承認してバッチを開始',
  testShotApproved: 'テスト撮影は承認済み',
  testShotRetake: '撮り直す',
  batchTitle: 'バッチ生成',
  batchWhy: '1 カットずつ生成し、確認してから次へ。途中で離れても大丈夫 —— 進捗は保存され、同じところから再開できます。',
  batchProgress: '承認済み',
  batchNext: '次のカットを生成',
  batchGenerating: '生成中、1〜3 分ほど……',
  batchApprove: '承認',
  batchRetry: '撮り直し',
  batchRetake: 'このカットを撮り直す',
  batchRetryMute: '無音で撮り直す（台詞は字幕で）',
  batchRetryMuteHint: 'この台詞はコンテンツ審査でブロックされました。無音撮り直しでは映像のみ生成し、台詞は字幕で表示します。脚本は変更されません。',
  rejectWhy: '原因として最も可能性が高い箇所',
  errorBlocked: 'このカットは Google のコンテンツ安全審査で拒否され、動画は生成されませんでした（今回は課金されません）。同じ内容で再試行しても再び拒否されます。',
  rejectSuggestion: '調整の提案',
  batchRetakeHint: '撮り直し後は再確認に戻ります。結合済みの映像は古くなるので、もう一度結合してください（$0）。',
  takesLabel: 'テイク',
  takesCurrent: '使用中',
  takesUse: 'このテイクを使う',
  takesSwitched: 'すべてのテイクを保存（切替は$0）。撮り直しが常に良いとは限りません。',
  batchReset: 'このセグメントを最初から',
  batchResetHint: '全カットの進捗を消し、キャラクターの声のロックも解除します。生成済みのファイルは残ります。',
  batchWaiting: '確認待ち',
  batchDone: 'このセグメントは全カット完了',
  batchDoneNext: '1本の動画に結合',
  batchDoneHint: '全カット承認済み。次は結合して字幕を付けます。',
  batchRemaining: '残り見込み',
  batchAttempts: '{n} テイク目',
  assembleTitle: '編集と字幕',
  assembleWhy: '生成したカットを 1 本の映像につなぎます。字幕は外付け —— 映像は一度作れば、字幕は何ヶ国語でも用意できます。',
  assembleRun: '1 本につなぐ',
  assembling: 'つないでいます……',
  assembleDownload: '映像をダウンロード',
  assembleSubtitles: '字幕',
  assembleSubtitleHint: '.srt を映像と同じフォルダに同じファイル名で置けば、多くのプレイヤーが自動で読み込みます。',
  promptsLanguage: 'プロンプトの言語',
  promptsLanguageNote:
    '言語を変えても書き換わるのは「画の説明」だけ。台詞はそのまま —— 台詞はキャラクターが実際に話す言葉なので、訳すと作品の中身が変わってしまう。他ツールで試すなら英語版が最も安定する。',
  promptsRewriting: '書き換え中……',
  promptsCurrentLanguage: '現在',
  warnings: '解析時の疑問点',
  back: '← 戻る',
  viewSource: '原文を見る',
  hideSource: '閉じる',
  minutes: '分',
  seconds: '秒',
  scenesUnit: 'シーン',
  loading: '読み込み中……',
  errorTitle: '問題が発生しました',
  retry: '再試行',
}

export const COPY: Record<Lang, Copy> = { zh, en, ja }

export const LANGS: { code: Lang; label: string }[] = [
  { code: 'zh', label: '中文' },
  { code: 'en', label: 'EN' },
  { code: 'ja', label: '日本語' },
]

/** 階段順序，給左側光柱用。與後端 Stage 對應。 */
export const STAGE_ORDER = [
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
]

/**
 * 階段說明。
 *
 * 為什麼放前端而不是用後端回的 `stage_description`：
 *    後端那份只有中文，切到日文介面時會殘留一句中文。
 *    階段說明是純介面文案、沒有邏輯，本來就該跟其他 UI 文字放在一起。
 *    後端的 stage_description 保留給 log 與 API 消費者用。
 */
export const STAGE_DESCRIPTIONS: Record<Lang, Record<string, string>> = {
  zh: {
    script_uploaded: '劇本已收到，尚未解析',
    parsed: '場次與角色已抓出',
    shots_planned: '鏡頭表已產生',
    look_chosen: '風格與畫面比例已定',
    tier_chosen: '畫質與預算已定',
    cast_locked: '所有角色已選定',
    prompts_ready: '提示詞已產生，可匯出',
    test_shot_passed: '試拍已通過',
    generating: '批次生成中',
    assembled: '接片與字幕完成',
  },
  en: {
    script_uploaded: 'Script received, not yet parsed',
    parsed: 'Scenes and characters extracted',
    shots_planned: 'Shot list ready',
    look_chosen: 'Style and aspect ratio set',
    tier_chosen: 'Quality tier and budget set',
    cast_locked: 'All characters cast',
    prompts_ready: 'Prompts ready to export',
    test_shot_passed: 'Test shot approved',
    generating: 'Generating shots',
    assembled: 'Cut and subtitled',
  },
  ja: {
    script_uploaded: '脚本を受領、未解析',
    parsed: 'シーンと登場人物を抽出済み',
    shots_planned: 'カット表が完成',
    look_chosen: '画風と画面比を決定',
    tier_chosen: '画質と予算を決定',
    cast_locked: '全キャラクターのキャスティング完了',
    prompts_ready: 'プロンプト書き出し可能',
    test_shot_passed: 'テスト撮影に合格',
    generating: '生成中',
    assembled: '編集・字幕完了',
  },
}

/**
 * 提示詞的分段標籤。
 *
 * key 來自後端 build_prompt 的組裝順序。使用者要看得出哪一段是角色、
 * 哪一段是物理約束，才知道要改什麼 —— 所以這些標籤不能寫死中文。
 */
export const PROMPT_SECTIONS: Record<Lang, Record<string, string>> = {
  zh: {
    character_tags: '角色外貌',
    style_prefix: '畫風',
    framing: '鏡別運鏡',
    description: '畫面描述',
    constraints: '物理約束',
    dialogue: '台詞',
    audio: '音效',
    negative: '負面約束',
  },
  en: {
    character_tags: 'Appearance',
    style_prefix: 'Style',
    framing: 'Framing & camera',
    description: 'Action',
    constraints: 'Physical constraints',
    dialogue: 'Dialogue',
    audio: 'Audio',
    negative: 'Negative',
  },
  ja: {
    character_tags: '人物の外見',
    style_prefix: '画風',
    framing: 'サイズ・カメラ',
    description: '画面の内容',
    constraints: '物理制約',
    dialogue: '台詞',
    audio: '音',
    negative: 'ネガティブ',
  },
}

export const STAGE_NAMES: Record<Lang, Record<string, string>> = {
  zh: {
    script_uploaded: '收件',
    parsed: '讀本',
    shots_planned: '分鏡',
    look_chosen: '定調',
    tier_chosen: '定案',
    cast_locked: '選角',
    prompts_ready: '提示詞',
    test_shot_passed: '試拍',
    generating: '開拍',
    assembled: '成片',
  },
  en: {
    script_uploaded: 'Received',
    parsed: 'Read-through',
    shots_planned: 'Breakdown',
    look_chosen: 'Look',
    tier_chosen: 'Budget',
    cast_locked: 'Casting',
    prompts_ready: 'Prompts',
    test_shot_passed: 'Test shot',
    generating: 'Shooting',
    assembled: 'Cut',
  },
  ja: {
    script_uploaded: '受領',
    parsed: '本読み',
    shots_planned: '絵コンテ',
    look_chosen: '画づくり',
    tier_chosen: '予算',
    cast_locked: 'キャスティング',
    prompts_ready: 'プロンプト',
    test_shot_passed: 'テスト撮影',
    generating: '撮影',
    assembled: '仕上げ',
  },
}
