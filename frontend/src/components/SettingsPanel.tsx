/**
 * 設定頁（Greenlight Local）：機器檢查 → 金鑰 → 每一步用哪個 AI → 影片引擎。
 *
 * 為什麼放在同一頁、由上往下排：這就是使用者第一次打開要走的順序 ——
 *   先知道「這台電腦能不能生影片」，再填金鑰，再決定誰做什麼。
 *   每一塊都顯示「現在是什麼狀態」，不讓人猜程式在幹嘛。
 *
 * 金鑰：只送出不取回。後端只回「有沒有、末四碼」，畫面上永遠看不到完整金鑰。
 * 驗證金鑰只問平台「列出模型」，不生成任何東西、不花錢。
 */
import { useEffect, useState } from 'react'
import type { Lang } from '../api'

type Check = { id: string; status: 'ok' | 'warn' | 'block' | 'missing'; label: string; detail: string; fix: string }
type Machine = { ready: boolean; can_install: boolean; checks: Check[] }
type RoleCfg = { provider: string; model: string }
type Settings = {
  keys: Record<string, { set: boolean; last4: string; error?: string }>
  keys_encrypted: boolean
  roles: Record<string, RoleCfg>
  role_table: { action: string; role: string; needs: string }[]
  engine: 'local' | 'cloud'
  comfy_url: string
}
type TestResult = { ok: boolean; reason?: string; models?: number; nemotron?: string[]; video_models?: string[] }

const T = {
  zh: {
    title: '設定', lede: '第一次使用照這個順序走：先看電腦能不能生影片，再填你自己的金鑰。',
    machine: '這台電腦', recheck: '重新檢查', checking: '檢查中…',
    ready: '可以生影片', canInstall: '缺的東西可以一鍵安裝', cannot: '這台電腦跑不動本機影片（可以改用雲端引擎）',
    keys: '你的金鑰', keysNote: '金鑰只存在你的電腦裡', encrypted: '（Windows 加密）', plain: '（未加密）',
    nebius: 'Nebius（Nemotron：寫分鏡、導演、製作人）', google: 'Google（看圖、聽聲音、雲端影片）',
    save: '存起來', remove: '刪掉', removeAsk: '確定要刪掉這把金鑰？刪掉之後要重新貼上才能用。', test: '驗證', testing: '驗證中…', saved: '已存，末四碼', none: '還沒填',
    testOk: (n: number, k: number) => `可以用：平台列出 ${n} 個模型，其中 Nemotron ${k} 個`,
    roles: '每一步用哪個 AI', rolesNote: 'Nemotron 只看得懂文字；要看圖、聽聲音的步驟請用 Google。',
    engine: '影片引擎', local: '本機（你的顯示卡）', cloud: '雲端（你的 Google 金鑰）', comfy: 'ComfyUI 位址', soon: 'Coming soon',
    status: { ok: '通過', warn: '沒驗證過', block: '不行', missing: '缺' },
    install: '一鍵安裝影片引擎', installNote: '會下載官方 ComfyUI 攜帶版（約 2 GB）和影片模型（約 42 GB）；已經有模型資料夾的話填進來就不用重下。',
    license: '影片模型 MiniMax H3 的授權不適用於這些地區：', licenseRead: '看授權全文', accept: '我讀過授權，而且不在上面這些地區使用',
    browse: '瀏覽…', open: '打開資料夾', storage: '存放位置', dataDir: '作品資料夾（專案、圖、影片、學習紀錄）', exportDir: '成品輸出資料夾（做好的影片另存一份在這）', free: '剩餘空間', change: '換位置', moveAsk: '要把現有作品一起搬過去嗎？（按「取消」＝新位置從空的開始，舊的留在原處）', moved: '已換到新位置',
    target: '安裝到哪個資料夾', reuse: '已經有模型資料夾（選填；空著會自動找電腦裡現成的）', installBtn: '開始安裝', installCancel: '取消安裝（下到一半的會留著，下次接著下）', installing: '安裝中…', installDone: '裝好了', installFailed: '安裝停下來了',
    img: '生圖與放大模型（選角、角色表、首幀、接幀修畫質、成片放大）', imgNote: 'Qwen-Image 2512 ＋ Real-ESRGAN 放大模型 —— 下載進你的 ComfyUI，不用自己開 ComfyUI 操作。商業可用（Apache 2.0／BSD-3）。已經有的檔不會重下；中斷了再按一次會接著下。',
    imgAccept: '我已閱讀並同意這個模型的授權（Apache 2.0）', imgWhere: 'ComfyUI 沒開，問不到模型放哪 —— 請選 ComfyUI 的 models 資料夾（裡面有 diffusion_models、loras…）',
    imgAsked: '已經問過 ComfyUI 模型放哪，會直接下載進去', imgHave: '已經有', imgNeed: '要下載', imgBtn: '下載生圖與放大模型', imgUpBtn: '只下載成片放大模型（85 MB）', imgDone: '生圖模型好了：設定頁③「生角色圖」選「本機」就會用它',
  },
  en: {
    title: 'Settings', lede: 'First time? Go top to bottom: check this computer, then add your own keys.',
    machine: 'This computer', recheck: 'Check again', checking: 'Checking…',
    ready: 'Ready to make video', canInstall: 'Missing pieces can be installed in one click', cannot: 'This computer cannot run the local video engine (use the cloud engine instead)',
    keys: 'Your keys', keysNote: 'Keys stay on this computer', encrypted: '(Windows-encrypted)', plain: '(not encrypted)',
    nebius: 'Nebius (Nemotron: shot lists, director, producer)', google: 'Google (images, audio, cloud video)',
    save: 'Save', remove: 'Remove', removeAsk: 'Remove this key? You will need to paste it again to use it.', test: 'Test', testing: 'Testing…', saved: 'Saved, ends in', none: 'Not set',
    testOk: (n: number, k: number) => `Works: the platform lists ${n} models, ${k} of them Nemotron`,
    roles: 'Which AI does each step', rolesNote: 'Nemotron reads text only; steps that look at images or listen to audio need Google.',
    engine: 'Video engine', local: 'Local (your GPU)', cloud: 'Cloud (your Google key)', comfy: 'ComfyUI address', soon: 'Coming soon',
    status: { ok: 'OK', warn: 'Untested', block: 'No', missing: 'Missing' },
    install: 'Install the video engine', installNote: 'Downloads the official ComfyUI portable build (about 2 GB) and the video models (about 42 GB). If you already have a models folder, enter it and nothing is downloaded twice.',
    license: 'The MiniMax H3 video model license does not cover:', licenseRead: 'Read the license', accept: 'I have read the license and will not use it in those regions',
    browse: 'Browse…', open: 'Open folder', storage: 'Where things are saved', dataDir: 'Projects folder (projects, images, videos, learning records)', exportDir: 'Finished-video folder (a copy of every finished film goes here)', free: 'free', change: 'Change', moveAsk: 'Move your existing projects to the new folder? (Cancel = start empty there; old projects stay where they are)', moved: 'Moved',
    target: 'Install folder', reuse: 'Existing models folder (optional — left empty, the app looks for models already on this computer)', installBtn: 'Install', installCancel: 'Cancel (partial downloads are kept and resume next time)', installing: 'Installing…', installDone: 'Installed', installFailed: 'Install stopped',
    img: 'Image and upscale models (casting, character sheets, first frames, cleaning chained frames, finishing in 1080p)', imgNote: 'Qwen-Image 2512 + Real-ESRGAN upscalers — downloaded straight into your ComfyUI; no need to touch ComfyUI yourself. Commercial use allowed (Apache 2.0 / BSD-3). Files you already have are skipped; if it stops, press again to resume.',
    imgAccept: 'I have read and accept this model’s license (Apache 2.0)', imgWhere: 'ComfyUI is not running, so it can’t tell us where models go — pick ComfyUI’s models folder (it contains diffusion_models, loras…)',
    imgAsked: 'Asked ComfyUI where its models go — downloading straight there', imgHave: 'have it', imgNeed: 'to download', imgBtn: 'Download the image and upscale models', imgUpBtn: 'Only the upscale models for finishing (85 MB)', imgDone: 'Image model ready: in ③, set “Character images” to Local to use it',
  },
  ja: {
    title: '設定', lede: '初めての方は上から順に：このPCで動画が作れるか確認して、ご自分のキーを入れてください。',
    machine: 'このパソコン', recheck: '再チェック', checking: 'チェック中…',
    ready: '動画を作れます', canInstall: '足りないものはワンクリックで入れられます', cannot: 'このPCではローカル動画は動きません（クラウドエンジンをお使いください）',
    keys: 'あなたのキー', keysNote: 'キーはこのPCの中だけに保存されます', encrypted: '（Windows で暗号化）', plain: '（暗号化なし）',
    nebius: 'Nebius（Nemotron：絵コンテ・監督・プロデューサー）', google: 'Google（画像・音声・クラウド動画）',
    save: '保存', remove: '削除', removeAsk: 'このキーを削除しますか？使うには再度貼り付けが必要です。', test: '確認', testing: '確認中…', saved: '保存済み・末尾', none: '未設定',
    testOk: (n: number, k: number) => `使えます：${n} モデル中 Nemotron ${k} 個`,
    roles: '各ステップの担当 AI', rolesNote: 'Nemotron は文字のみ。画像を見る・音声を聞くステップは Google にしてください。',
    engine: '動画エンジン', local: 'ローカル（あなたのGPU）', cloud: 'クラウド（あなたの Google キー）', comfy: 'ComfyUI のアドレス', soon: 'Coming soon',
    status: { ok: 'OK', warn: '未検証', block: '不可', missing: '不足' },
    install: '動画エンジンをワンクリックで導入', installNote: '公式 ComfyUI ポータブル版（約 2GB）と動画モデル（約 42GB）をダウンロードします。モデルのフォルダーがあれば入力すると再ダウンロードしません。',
    license: '動画モデル MiniMax H3 のライセンス対象外の地域：', licenseRead: 'ライセンス全文', accept: 'ライセンスを読み、これらの地域では使用しません',
    browse: '参照…', open: 'フォルダを開く', storage: '保存場所', dataDir: '作品フォルダー（プロジェクト・画像・動画・学習記録）', exportDir: '完成動画の出力先（完成した動画のコピーをここに保存）', free: '空き', change: '変更', moveAsk: '既存の作品も新しい場所へ移しますか？（キャンセル＝新しい場所は空から、古い作品は元の場所に残ります）', moved: '移動しました',
    target: 'インストール先', reuse: '既存のモデルフォルダー（任意。空欄ならパソコン内の既存モデルを自動で探します）', installBtn: 'インストール', installCancel: 'キャンセル（途中のダウンロードは残り、次回続きから）', installing: 'インストール中…', installDone: '完了', installFailed: '停止しました',
    img: '画像・アップスケールモデル（キャスティング・キャラ表・最初のフレーム・つなぎフレームの補正・1080p 仕上げ）', imgNote: 'Qwen-Image 2512 ＋ Real-ESRGAN アップスケーラー —— あなたの ComfyUI に直接ダウンロードします。ComfyUI を操作する必要はありません。商用利用可（Apache 2.0／BSD-3）。既にあるファイルはスキップ、中断したらもう一度押せば続きから。',
    imgAccept: 'このモデルのライセンス（Apache 2.0）を読み、同意します', imgWhere: 'ComfyUI が起動していないため保存先を聞けません —— ComfyUI の models フォルダー（diffusion_models、loras… が入っている）を選んでください',
    imgAsked: 'ComfyUI に保存先を確認済み —— そこへ直接ダウンロードします', imgHave: 'あり', imgNeed: 'ダウンロード', imgBtn: '画像・アップスケールモデルをダウンロード', imgUpBtn: '仕上げ用アップスケールモデルだけ（85 MB）', imgDone: '画像モデルの準備完了：③の「キャラクター画像」を「ローカル」にすると使われます',
  },
}

const NEMOTRON = [
  'nvidia/nemotron-3-super-120b-a12b',
  'nvidia/Nemotron-3-Ultra-550b-a55b',
  'nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B',
]

const BADGE: Record<Check['status'], string> = { ok: 'done', warn: 'in-progress', block: 'over', missing: 'not-started' }

/** 機器檢查的項目名稱與建議（後端給 id 和英文；畫面照使用者選的語言顯示。數字、型號是資料，原樣顯示）。 */
const CHECK_TEXT: Record<Lang, Record<string, { label: string; fix?: Partial<Record<Check['status'], string>>; detail?: Partial<Record<Check['status'], string>> }>> = {
  zh: {
    os: { label: 'Windows 64 位元', fix: { block: '本機影片目前只支援 Windows；雲端版開發中。' } },
    gpu: { label: 'NVIDIA 顯示卡', fix: { block: '需要至少 24 GB 顯存的 NVIDIA 顯示卡；雲端版開發中。' } },
    vram: { label: '顯存至少 24 GB', fix: { block: '低於驗證過的最低需求（作者的 RTX 5090 Laptop 24 GB）。' } },
    gpu_gen: { label: '顯示卡世代', fix: { warn: '這個世代沒測過；安裝時會改用 int8 文字編碼器。', block: '太舊，跑不動量化過的影片模型。' } },
    driver: { label: '驅動支援 CUDA 13', fix: { block: '請更新 NVIDIA 驅動。' } },
    ram: { label: '記憶體', fix: { warn: '低於 32 GB 沒測過（作者是 64 GB）。' } },
    disk: { label: '硬碟剩餘空間', fix: { block: '模型約 42 GB，至少要留 50 GB，或換一顆硬碟。' } },
    ffmpeg: { label: 'ffmpeg（接片用）', fix: { missing: '安裝時會一起裝。' } },
    comfyui: { label: 'ComfyUI 影片引擎', fix: { missing: '在 app 裡一鍵安裝，或啟動你自己的 ComfyUI 並填它的位址。' } },
    nodes: { label: '管線節點', fix: { missing: '請更新 ComfyUI（新版內建 MiniMax H3 節點）。' } },
    model: { label: '模型', fix: { missing: '安裝時會從 Hugging Face 下載，並核對校驗碼。' } },
    verify_tools: { label: '生成後查證工具', detail: { ok: '語音辨識、假名讀音、拼音都可以用' } },
  },
  en: {},
  ja: {
    os: { label: 'Windows 64ビット', fix: { block: 'ローカル動画は現在 Windows のみ。クラウド版は開発中です。' } },
    gpu: { label: 'NVIDIA GPU', fix: { block: 'VRAM 24GB 以上の NVIDIA GPU が必要です。クラウド版は開発中です。' } },
    vram: { label: 'VRAM 24GB 以上', fix: { block: '検証済みの最低条件（作者の RTX 5090 Laptop 24GB）を下回っています。' } },
    gpu_gen: { label: 'GPU の世代', fix: { warn: 'この世代は未検証です。int8 のテキストエンコーダーを使います。', block: '古すぎて量子化モデルが動きません。' } },
    driver: { label: 'ドライバーが CUDA 13 対応', fix: { block: 'NVIDIA ドライバーを更新してください。' } },
    ram: { label: 'メモリ', fix: { warn: '32GB 未満は未検証です（作者は 64GB）。' } },
    disk: { label: 'ディスクの空き', fix: { block: 'モデルは約 42GB。50GB 以上空けるか別のドライブを選んでください。' } },
    ffmpeg: { label: 'ffmpeg（つなぎ用）', fix: { missing: 'インストール時に一緒に入れます。' } },
    comfyui: { label: 'ComfyUI 動画エンジン', fix: { missing: 'アプリからワンクリックで入れるか、ご自分の ComfyUI のアドレスを入れてください。' } },
    nodes: { label: 'パイプラインのノード', fix: { missing: 'ComfyUI を更新してください（MiniMax H3 ノードは新しい版に内蔵）。' } },
    verify_tools: { label: '生成後の検証ツール', detail: { ok: '音声認識・かな読み・ピンインが使えます' } },
    model: { label: 'モデル', fix: { missing: 'インストール時に Hugging Face から取得し、チェックサムを確認します。' } },
  },
}

/** 「每一步用哪個 AI」的步驟名稱（後端給的是內部角色代號）。 */
const ROLE_TEXT: Record<Lang, Record<string, string>> = {
  zh: { parse: '解析劇本', segment: '分段', staging: '場面調度（站位、道具）', director: '分鏡（導演）', prompts: '寫提示詞', screening: '內容預審',
        subtitles: '翻譯字幕', learning: '提煉經驗', frame_review: '看片檢核（要看圖）', voice: '分析音色（要聽聲音）', character: '角色外貌與聲音描述', casting: '生角色圖（沒有生圖模型就只出文字描述）', lines: '台詞導演（學語言模式：口語、讀音、雙語字幕）', critic: '台詞審稿（學語言模式：對照原文抓意思跑掉的句子）', doctor: '查證診斷（生完沒過：對照症狀庫找原因、提修法）' },
  en: { parse: 'Read the script', segment: 'Split into parts', staging: 'Staging (spots, props)', director: 'Shot list (director)', prompts: 'Write prompts',
        screening: 'Content check', subtitles: 'Translate subtitles', learning: 'Learn from past runs',
        frame_review: 'Review frames (sees images)', voice: 'Voice profile (hears audio)', character: 'Character looks & voice (text)', casting: 'Character images (text-only if no image model)', lines: 'Line director (study mode: spoken register, readings, dual subtitles)', critic: 'Line review (study mode: lines whose meaning drifted from the source)', doctor: 'Check diagnosis (failed checks → symptom library → fix)' },
  ja: { parse: '台本の解析', segment: '分割', staging: '段取り（立ち位置・小道具）', director: '絵コンテ（監督）', prompts: 'プロンプト作成', screening: '内容チェック',
        subtitles: '字幕の翻訳', learning: '経験の抽出', frame_review: 'フレーム確認（画像を見る）', voice: '声の分析（音声を聞く）', character: 'キャラクターの外見と声（テキスト）', casting: 'キャラクター画像（画像モデルがなければテキストのみ）', lines: 'セリフ演出（学習モード：口語・読み・二言語字幕）', critic: 'セリフの確認（学習モード：原文と意味がずれた行）', doctor: '検証の診断（不合格 → 症状ライブラリ → 修正案）' },
}

function checkText(lang: Lang, c: Check): { label: string; fix: string } {
  const key = c.id.startsWith('model:') ? 'model' : c.id
  const entry = CHECK_TEXT[lang][key]
  if (!entry) return { label: c.label, fix: c.fix }
  const label = key === 'model' ? `${entry.label} ${c.id.slice(6)}` : entry.label
  return { label, fix: entry.fix?.[c.status] ?? (c.fix ? c.fix : '') }
}

export function SettingsPanel({ lang }: { lang: Lang }) {
  const t = T[lang]
  const [s, setS] = useState<Settings | null>(null)
  const [m, setM] = useState<Machine | null>(null)
  const [checking, setChecking] = useState(false)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [tests, setTests] = useState<Record<string, TestResult | 'running'>>({})
  const [lic, setLic] = useState<{ url: string; excluded_regions: string[] } | null>(null)
  const [accepted, setAccepted] = useState(false)
  const [target, setTarget] = useState('C:\\GreenlightLocal')
  const [reuse, setReuse] = useState('')
  const [store, setStore] = useState<{ data_dir: string; export_dir: string; free_gb: number } | null>(null)
  const pickFolder = async (initial?: string): Promise<string | null> => {
    const r = await fetch('/api/machine/pick-folder', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ initial: initial ?? null }) })
    return (await r.json()).path ?? null
  }
  const openFolder = (path: string) => void fetch('/api/machine/open-folder', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path }) })
  const putStorage = async (body: Record<string, unknown>) => {
    const r = await fetch('/api/settings/storage', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body) })
    if (r.ok) setStore(await r.json())
  }
  const changeDataDir = async () => {
    const p = await pickFolder(store?.data_dir)
    if (p) await putStorage({ data_dir: p, move: window.confirm(t.moveAsk) })
  }
  const changeExportDir = async () => {
    const p = await pickFolder(store?.export_dir)
    if (p) await putStorage({ export_dir: p })
  }
  const [inst, setInst] = useState<{ status: string | null; log: { step: string; status: string; detail?: string; progress?: number }[] } | null>(null)
  const pollInstall = async () => {
    const r: typeof inst = await (await fetch('/api/machine/install')).json()
    setInst(r)
    if (r?.status === 'running') setTimeout(() => void pollInstall(), 2000)
    else if (r?.status === 'done') void check()
  }
  const install = async () => {
    await fetch('/api/machine/install', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target, accept_license: accepted, reuse_models: reuse || null }) })
    void pollInstall()
  }
  // 生圖模型包：app 問 ComfyUI 模型放哪、自己下載（使用者不用懂 ComfyUI）
  type ImgInfo = { license_url: string; comfy_answering: boolean; total_gb: number
    files: { name: string; folder: string; gb: number; present: boolean | null }[]; job: NonNullable<typeof inst> }
  const [img, setImg] = useState<ImgInfo | null>(null)
  const [imgAccepted, setImgAccepted] = useState(false)
  const [imgDir, setImgDir] = useState('')
  const pollImg = async (dir?: string) => {
    const q = dir ? `?models_dir=${encodeURIComponent(dir)}` : ''
    const r: ImgInfo = await (await fetch(`/api/machine/image-models${q}`)).json()
    setImg(r)
    if (r.job?.status === 'running') setTimeout(() => void pollImg(dir), 2000)
  }
  // onlyUpscale：只要成片放大（Real-ESRGAN），不要 Qwen 生圖 40 GB（範例 3：成片退回 lanczos，只缺這 85 MB）
  const installImg = async (onlyUpscale = false) => {
    await fetch('/api/machine/image-models', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ accept_license: imgAccepted, models_dir: imgDir || null, only_upscale: onlyUpscale }) })
    void pollImg(imgDir || undefined)
  }
  useEffect(() => {
    void pollImg()
    void (async () => setLic(await (await fetch('/api/machine/license')).json()))()
    void (async () => setStore(await (await fetch('/api/settings/storage')).json()))()
    void pollInstall()
  }, [])

  const load = async () => setS(await (await fetch('/api/settings')).json())
  const check = async () => {
    setChecking(true)
    try {
      setM(await (await fetch('/api/machine/check')).json())
    } finally {
      setChecking(false)
    }
  }
  useEffect(() => {
    void load()
    void check()
  }, [])

  const put = async (path: string, body: unknown) => {
    const r = await fetch(`/api/settings/${path}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    })
    setS(await r.json())
  }
  const saveKey = async (provider: string, key: string) => {
    await put('key', { provider, key })
    setDraft({ ...draft, [provider]: '' })
    setTests({ ...tests, [provider]: undefined as never })
  }
  const test = async (provider: string) => {
    setTests({ ...tests, [provider]: 'running' })
    const r: TestResult = await (await fetch(`/api/settings/test/${provider}`, { method: 'POST' })).json()
    setTests({ ...tests, [provider]: r })
  }

  if (!s) return <p className="lede">{t.checking}</p>

  return (
    <div className="settings-panel">
      <p className="eyebrow">Greenlight Local · NVIDIA Nemotron Edition</p>
      <h1>{t.title}</h1>
      <p className="lede">{t.lede}</p>

      {/* ① 這台電腦 */}
      <section className="section">
        <div className="section-head">
          <h2>① {t.machine}</h2>
          <button className="btn small" onClick={() => void check()} disabled={checking}>
            {checking ? t.checking : t.recheck}
          </button>
        </div>
        {m && (
          <div className={`notice ${m.ready ? '' : 'warn'}`}>
            <strong>{m.ready ? t.ready : m.can_install ? t.canInstall : t.cannot}</strong>
          </div>
        )}
        {m && (
          <table className="ledger-table settings-table">
            <tbody>
              {m.checks.map((c) => {
                const ct = checkText(lang, c)
                return (
                  <tr key={c.id}>
                    <td><span className={`badge ${BADGE[c.status]}`}>{t.status[c.status]}</span></td>
                    <td>{ct.label}</td>
                    <td>{CHECK_TEXT[lang][c.id]?.detail?.[c.status] ?? c.detail}{ct.fix && <div style={{ color: 'var(--sumi-faint)', marginTop: 4 }}>→ {ct.fix}</div>}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </section>

      {/* ①-2 一鍵安裝：電腦夠力、但缺 ComfyUI 或模型時才出現 */}
      {m && !m.ready && m.can_install && (
        <section className="section">
          <div className="section-head"><h2>{t.install}</h2></div>
          <p className="lede">{t.installNote}</p>
          {lic && (
            <div className="notice warn">
              {t.license} {lic.excluded_regions.join('、')} · <a href={lic.url} target="_blank" rel="noreferrer">{t.licenseRead}</a>
              <label style={{ display: 'flex', gap: 8, marginTop: 10, alignItems: 'center' }}>
                <input type="checkbox" checked={accepted} onChange={(e) => setAccepted(e.target.checked)} />
                {t.accept}
              </label>
            </div>
          )}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, margin: '12px 0' }}>
            <label>{t.target}</label>
            <div style={{ display: 'flex', gap: 8 }}>
              <input className="settings-input" value={target} onChange={(e) => setTarget(e.target.value)} />
              <button className="btn small" onClick={() => void pickFolder(target).then((p) => p && setTarget(p))}>{t.browse}</button>
            </div>
            <label>{t.reuse}</label>
            <div style={{ display: 'flex', gap: 8 }}>
              <input className="settings-input" value={reuse} placeholder="D:\models" onChange={(e) => setReuse(e.target.value)} />
              <button className="btn small" onClick={() => void pickFolder(reuse || undefined).then((p) => p && setReuse(p))}>{t.browse}</button>
            </div>
          </div>
          <div className="actions">
            <button className="btn primary" disabled={!accepted || inst?.status === 'running'} onClick={() => void install()}>
              {inst?.status === 'running' ? t.installing : t.installBtn}
            </button>
            {inst?.status === 'running' && (
              // ：以前沒有取消鈕，按錯只能把 app 關掉
              <button className="btn ghost" onClick={() => void fetch('/api/machine/install/cancel', { method: 'POST' }).then(() => pollInstall())}>
                {t.installCancel}
              </button>
            )}
          </div>
          {inst?.log && inst.log.length > 0 && (
            <ul className="direction-log">
              {inst.log.map((e, i) => (
                <li key={i} className={e.status === 'done' ? 'ok' : e.status === 'failed' ? 'bad' : ''}>
                  <strong>{e.step}</strong> · {e.detail}{typeof e.progress === 'number' && ` (${Math.round(e.progress * 100)}%)`}
                </li>
              ))}
            </ul>
          )}
          {inst?.status === 'done' && <div className="notice"><strong>{t.installDone}</strong></div>}
          {inst?.status === 'failed' && <div className="notice warn"><strong>{t.installFailed}</strong></div>}
        </section>
      )}

      {/* ①-3 生圖模型：下載進使用者的 ComfyUI（問 ComfyUI 放哪；沒開就讓使用者選資料夾） */}
      {img && (
        <section className="section">
          <div className="section-head"><h2>{t.img}</h2><span className="section-note">{img.total_gb} GB</span></div>
          <p className="lede">{t.imgNote}</p>
          <table className="ledger-table settings-table">
            <tbody>
              {img.files.map((f) => (
                <tr key={f.name}>
                  <td><span className={`badge ${f.present ? BADGE.ok : BADGE.missing}`}>{f.present ? t.imgHave : t.imgNeed}</span></td>
                  <td>{f.name}</td>
                  <td>{f.folder} · {f.gb} GB</td>
                </tr>
              ))}
            </tbody>
          </table>
          {img.comfy_answering ? (
            <p className="section-note">{t.imgAsked}</p>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, margin: '12px 0' }}>
              <label>{t.imgWhere}</label>
              <div style={{ display: 'flex', gap: 8 }}>
                <input className="settings-input" value={imgDir} placeholder="D:\ComfyUI\models" onChange={(e) => setImgDir(e.target.value)} onBlur={() => imgDir && void pollImg(imgDir)} />
                <button className="btn small" onClick={() => void pickFolder(imgDir || undefined).then((p) => { if (p) { setImgDir(p); void pollImg(p) } })}>{t.browse}</button>
              </div>
            </div>
          )}
          <div className="notice">
            <a href={img.license_url} target="_blank" rel="noreferrer">{t.licenseRead}</a>
            <label style={{ display: 'flex', gap: 8, marginTop: 10, alignItems: 'center' }}>
              <input type="checkbox" checked={imgAccepted} onChange={(e) => setImgAccepted(e.target.checked)} />
              {t.imgAccept}
            </label>
          </div>
          <div className="actions">
            <button className="btn primary"
              disabled={!imgAccepted || img.job?.status === 'running' || (!img.comfy_answering && !imgDir)}
              onClick={() => void installImg()}>
              {img.job?.status === 'running' ? t.installing : t.imgBtn}
            </button>
            <button className="btn ghost"
              disabled={!imgAccepted || img.job?.status === 'running' || (!img.comfy_answering && !imgDir)}
              onClick={() => void installImg(true)}>
              {t.imgUpBtn}
            </button>
          </div>
          {img.job?.log && img.job.log.length > 0 && (
            <ul className="direction-log">
              {img.job.log.map((e, i) => (
                <li key={i} className={e.status === 'done' ? 'ok' : e.status === 'failed' ? 'bad' : ''}>
                  <strong>{e.step}</strong> · {e.detail}{typeof e.progress === 'number' && ` (${Math.round(e.progress * 100)}%)`}
                </li>
              ))}
            </ul>
          )}
          {img.job?.status === 'done' && <div className="notice"><strong>{t.imgDone}</strong></div>}
          {img.job?.status === 'failed' && <div className="notice warn"><strong>{t.installFailed}</strong></div>}
        </section>
      )}

      {/* ② 金鑰 */}
      <section className="section">
        <div className="section-head">
          <h2>② {t.keys}</h2>
          <span className="section-note">{t.keysNote}{s.keys_encrypted ? t.encrypted : t.plain}</span>
        </div>
        {(['nebius', 'google'] as const).map((p) => {
          const k = s.keys[p]
          const r = tests[p]
          // 雲端入口不開放（多一個東西要維護）→ Google 那一列灰掉、標 Coming soon
          if (p === 'google') return (
            <div key={p} className="prompt-row coming-soon" style={{ marginBottom: 18 }}>
              <div className="prompt-head"><strong>{t[p]}</strong><span className="badge not-started" style={{ marginLeft: 10 }}>{t.soon}</span></div>
            </div>
          )
          return (
            <div key={p} className="prompt-row" style={{ marginBottom: 18 }}>
              <div className="prompt-head"><strong>{t[p]}</strong>
                <span className={`badge ${k?.set ? 'done' : 'not-started'}`} style={{ marginLeft: 10 }}>
                  {k?.set ? `${t.saved} ${k.last4}` : t.none}
                </span>
              </div>
              <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
                <input type="password" autoComplete="off" value={draft[p] ?? ''} placeholder="sk-…"
                  onChange={(e) => setDraft({ ...draft, [p]: e.target.value })} className="settings-input" />
                <button className="btn small primary" disabled={!draft[p]} onClick={() => void saveKey(p, draft[p])}>{t.save}</button>
                {k?.set && <button className="btn small" onClick={() => void test(p)}>{r === 'running' ? t.testing : t.test}</button>}
                {k?.set && <button className="btn small ghost" onClick={() => { if (window.confirm(t.removeAsk)) void saveKey(p, '') }}>{t.remove}</button>}
              </div>
              {r && r !== 'running' && (
                <div className={`notice ${r.ok ? '' : 'warn'}`} style={{ marginTop: 8 }}>
                  {r.ok ? t.testOk(r.models ?? 0, r.nemotron?.length ?? 0) : r.reason}
                </div>
              )}
            </div>
          )
        })}
      </section>

      {/* ③ 每一步用哪個 AI */}
      <section className="section">
        <div className="section-head"><h2>③ {t.roles}</h2><span className="section-note">{t.rolesNote}</span></div>
        <table className="ledger-table settings-table">
          <tbody>
            {s.role_table.map((row) => {
              const cfg = s.roles[row.role] ?? { provider: 'nebius', model: NEMOTRON[0] }
              const textOnly = row.needs === 'text'
              return (
                <tr key={row.role}>
                  <td>{ROLE_TEXT[lang][row.role] ?? row.action}</td>
                  <td>
                    <select value={cfg.provider} onChange={(e) => void put('roles', {
                      roles: { [row.role]: { provider: e.target.value, model: e.target.value === 'nebius' ? NEMOTRON[0] : '' } },
                    })}>
                      {textOnly && <option value="nebius">Nemotron</option>}
                      <option value="google" disabled>Google（{t.soon}）</option>
                      {row.needs === 'image' && <option value="local">{t.local}</option>}
                    </select>
                  </td>
                  <td>
                    {cfg.provider === 'nebius' && (
                      <select value={cfg.model} onChange={(e) => void put('roles', {
                        roles: { [row.role]: { provider: 'nebius', model: e.target.value } },
                      })}>
                        {NEMOTRON.map((n) => <option key={n} value={n}>{n.split('/')[1]}</option>)}
                      </select>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </section>

      {/* ④ 影片引擎 */}
      <section className="section">
        <div className="section-head"><h2>④ {t.engine}</h2></div>
        <div className="choices">
          {(['local', 'cloud'] as const).map((e) => (
            <button key={e} className={`choice ${s.engine === e && e === 'local' ? 'on' : ''}${e === 'cloud' ? ' coming-soon' : ''}`}
              aria-pressed={s.engine === e} disabled={e === 'cloud'}
              onClick={() => e === 'local' && void put('params', { engine: e })}>
              <span className="choice-label">{t[e]}</span>
              {e === 'cloud' && <span className="choice-note">{t.soon}</span>}
            </button>
          ))}
        </div>
        {s.engine === 'local' && (
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 12 }}>
            <span>{t.comfy}</span>
            <input defaultValue={s.comfy_url} onBlur={(e) => void put('params', { comfy_url: e.target.value })} />
          </div>
        )}
      </section>

      {/* ⑤ 存放位置：作品放哪、成品輸出到哪（一般程式的習慣：自己選資料夾） */}
      {store && (
        <section className="section">
          <div className="section-head"><h2>⑤ {t.storage}</h2></div>
          {([['data', t.dataDir, store.data_dir, changeDataDir], ['export', t.exportDir, store.export_dir, changeExportDir]] as const).map(([k, label, path, change]) => (
            <div key={k} className="prompt-row" style={{ marginBottom: 14 }}>
              <div className="prompt-head"><strong>{label}</strong>
                {k === 'data' && <span className="section-note" style={{ marginLeft: 10 }}>{t.free} {store.free_gb} GB</span>}</div>
              <div style={{ display: 'flex', gap: 8, marginTop: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                <code style={{ flex: '1 1 240px', overflowWrap: 'anywhere' }}>{path}</code>
                <button className="btn small" onClick={() => void change()}>{t.change}</button>
                <button className="btn small ghost" onClick={() => openFolder(path)}>{t.open}</button>
              </div>
            </div>
          ))}
        </section>
      )}

      {/* ⑥ 知識庫：症狀庫、導演規則放在安裝檔外面，隨時可改（安裝檔只是方便的外殼） */}
      <KnowledgeSection lang={lang} openFolder={openFolder} />
    </div>
  )
}

const KT = {
  zh: { title: '⑥ 知識庫（隨時可以改）', note: '症狀庫和導演規則放在安裝檔外面：用任何文字編輯器改、存檔，下一鏡就用新的，不用重開也不用重新打包。改壞了會自動退回預設版並在這裡講哪裡壞。',
        open: '打開資料夾', reset: '還原預設', edited: '已修改', original: '預設版', broken: '格式壞了，正在用預設版：',
        names: { 'symptoms.yaml': '症狀庫（查證沒過時 agent 唯一能引用的知識）', 'directing_rules.md': '導演規則（Nemotron 寫分鏡照這份、關卡也照這份）' } as Record<string, string> },
  en: { title: '⑥ Knowledge (editable any time)', note: 'The symptom library and the director rules live outside the installer: edit them in any text editor and save — the next shot uses them, no restart or rebuild. A broken edit falls back to the default and is reported here.',
        open: 'Open folder', reset: 'Restore default', edited: 'edited', original: 'default', broken: 'Broken — using the default for now: ',
        names: { 'symptoms.yaml': 'Symptom library (the only knowledge the agent may cite when a check fails)', 'directing_rules.md': 'Director rules (Nemotron writes shots by these; the gates check them)' } as Record<string, string> },
  ja: { title: '⑥ ナレッジ（いつでも編集可）', note: '症状ライブラリと演出ルールはインストーラーの外にあります。テキストエディタで編集・保存すれば次のカットから反映、再起動も再ビルドも不要。壊れた場合は既定版に戻し、ここに表示します。',
        open: 'フォルダを開く', reset: '既定に戻す', edited: '編集済み', original: '既定', broken: '形式エラー、既定版を使用中：',
        names: { 'symptoms.yaml': '症状ライブラリ（検証不合格時にエージェントが引用できる唯一の知識）', 'directing_rules.md': '演出ルール（Nemotron の絵コンテと関門の基準）' } as Record<string, string> },
}

function KnowledgeSection({ lang, openFolder }: { lang: Lang; openFolder: (p: string) => void }) {
  const t = KT[lang]
  const [k, setK] = useState<{ dir: string; files: { name: string; path: string; edited: boolean; error: string }[] } | null>(null)
  const load = async () => { const r = await fetch('/api/settings/knowledge'); if (r.ok) setK(await r.json()) }
  useEffect(() => { void load() }, [])
  if (!k) return null
  const reset = async (name: string) => {
    const r = await fetch('/api/settings/knowledge/reset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) })
    if (r.ok) setK(await r.json())
  }
  return (
    <section className="section">
      <div className="section-head"><h2>{t.title}</h2></div>
      <p className="lang-lead">{t.note}</p>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
        <code style={{ flex: '1 1 240px', overflowWrap: 'anywhere' }}>{k.dir}</code>
        <button className="btn small ghost" onClick={() => openFolder(k.dir)}>{t.open}</button>
      </div>
      {k.files.map((f) => (
        <div key={f.name} className="prompt-row" style={{ marginBottom: 10 }}>
          <div className="prompt-head"><strong>{f.name}</strong>
            <span className={`badge ${f.edited ? 'in-progress' : 'not-started'}`} style={{ marginLeft: 10 }}>{f.edited ? t.edited : t.original}</span></div>
          <div className="section-note">{t.names[f.name] ?? ''}</div>
          {f.error && <div className="notice warn">{t.broken}{f.error}</div>}
          {f.edited && <button className="btn small ghost" onClick={() => void reset(f.name)}>{t.reset}</button>}
        </div>
      ))}
    </section>
  )
}
