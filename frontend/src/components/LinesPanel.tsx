/**
 * 台詞導演（學語言模式，段落層）：分鏡表裡的台詞 → Nemotron 改寫成原聲語言的口語 → 關卡 → 學習筆記 → 學習字幕版。
 *
 * 畫面要讓人看得到三件事：
 *   1. 程式在做什麼、做到哪（跟導演進度同一套：步驟條＋人話紀錄）
 *   2. 每一句「原句 → 導過的台詞」並排，加上語氣標籤和「為什麼這樣說」——這就是跟機翻不一樣的地方
 *   3. 字幕實際長怎樣（上行原聲＋讀音、下行母語），影片還沒生也看得到
 * 使用者可以自己改任何一句（改完重跑關卡、寫回分鏡表，H3 唸的就是改過的）。
 */
import { useEffect, useRef, useState } from 'react'
import { makePollGate } from '../pollGate'
import { send, type Lang } from '../api'
import { LANG_NAME } from './LanguagePanel'
import { AiText } from './AiText'

type Line = {
  id: string; shot_id: string; speaker: string; source: string; ruby: string; spoken: string; register: string
  why: string; subs: Record<string, string>; hints: string[]; issues: { level: string; rule: string; zh: string }[]; edited?: boolean
  say?: string
}
type Note = { line_id: string; point: string; explain: string; textbook: string; examples: string[] }
type LogEntry = { t: number; kind: string; [k: string]: unknown }
type State = {
  study_mode: boolean; status: string | null; log: LogEntry[]; lines: Line[]; notes: Note[]; film_language: string; learner_languages: string[]
  has_shot_list: boolean; shots_with_dialogue: number; has_video: boolean
  study_videos: Record<string, { status: string; uri?: string; error?: string; running?: boolean }>
}

const REG: Record<Lang, Record<string, string>> = {
  zh: { intimate: '親密', casual: '口語', polite: '禮貌', formal: '正式', honorific: '敬語', narration: '旁白' },
  en: { intimate: 'intimate', casual: 'casual', polite: 'polite', formal: 'formal', honorific: 'honorific', narration: 'narration' },
  ja: { intimate: '親密', casual: 'タメ口', polite: '丁寧語', formal: '改まった', honorific: '敬語', narration: 'ナレーション' },
}
const RULE: Record<Lang, Record<string, string>> = {
  zh: { ruby: '讀音沒標齊', kougo: '私下講話卻是書面語', length: '這一鏡唸不完', subs: '字幕缺或照抄', target: '還沒改成原聲語言', ids: '漏句或多句', tongue: '可能唸糊', short: '太短會唸成亂音',
        align: '對錯句（併句或錯位）', names: '名字被翻掉', persona: '人稱不合角色（女生說俺）', foreign: '混了外文單字', register: '語氣標籤不合法', reading: '讀音可能錯（字典對照）', explain: '解說沒用學習者的語言寫',
        ruby_copy: '加讀音時把字改掉了', okurigana: '讀音吃掉了送假名', untranslated: '夾了沒翻的中文', grammar: '動詞活用接錯', terms: '術語沒照名字表' },
  en: { ruby: 'missing readings', kougo: 'written, not spoken, Japanese', length: 'too long for the shot', subs: 'subtitle missing or copied', target: 'not in the spoken language yet', ids: 'missing or extra lines', tongue: 'hard to pronounce', short: 'too short (gets garbled)',
        align: 'answered the wrong line (merged or shifted)', names: 'a name got translated', persona: 'voice does not fit the character', foreign: 'stray foreign words', register: 'invalid register', reading: 'reading may be wrong (dictionary)', explain: 'explanation not in the learner\'s language',
        ruby_copy: 'adding readings changed the words', okurigana: 'reading swallowed the okurigana', untranslated: 'untranslated Chinese left in', grammar: 'verb form does not conjugate', terms: 'term not as in the glossary' },
  ja: { ruby: 'ふりがな不足', kougo: '会話なのに書き言葉', length: 'カット内に言い切れない', subs: '字幕の欠落・コピー', target: 'まだ話す言語になっていない', ids: 'セリフの欠落・重複', tongue: '言いにくい', short: '短すぎて崩れる',
        align: '別の行に答えた（結合・ずれ）', names: '名前が訳された', persona: '人称がキャラに合わない', foreign: '外国語の混入', register: '口調ラベルが不正', reading: '読みの誤りの可能性（辞書）', explain: '解説が学習者の言語でない',
        ruby_copy: 'ふりがなを付けたら字が変わった', okurigana: '読みが送り仮名を含んでいる', untranslated: '訳していない中国語が残っている', grammar: '活用の誤り', terms: '用語が名前リストと違う' },
}

const T = {
  zh: {
    title: '台詞導演（Nemotron · 學語言模式）', start: '導台詞', restart: '重新導台詞', running: '進行中', done: '完成', failed: '失敗',
    steps: ['收集台詞', '改寫＋關卡', '學習筆記', '完成'], elapsed: '已經', sec: '秒', min: '分',
    needShots: '先在上面導完分鏡，才有台詞可以導。',
    lead: (n: number, film: string) => `這一段有 ${n} 鏡有台詞。Nemotron 會照角色關係與情境，把每一句改寫成${film}口語，標讀音、寫字幕，再由程式關卡檢查（讀音齊不齊、口語夠不夠、這一鏡唸不唸得完）。`,
    start_: (n: number) => `開始：${n} 句台詞`, round: (r: number, n: number, e: number) => `第 ${r} 版：${n} 句，關卡擋下 ${e} 處`,
    passed: (r: number, n: number) => `第 ${r} 版：${n} 句，關卡全過`, writing: (r: number) => `Nemotron 正在寫第 ${r} 版`,
    finished: (n: number, notes: number, left: number, doubts = 0) => `完成：${n} 句、學習筆記 ${notes} 條${left ? `，還有 ${left} 處沒改掉（標紅，請自己看）` : ''}${doubts ? `；${doubts} 個讀音跟字典不一樣，請確認（標橘）` : ''}`,
    mostly: '最多的是', colWho: '誰', colSource: '劇本原句', colSpoken: '導過的台詞', colWhy: '為什麼這樣說', edit: '改', save: '存', cancel: '取消',
    preview: '字幕預覽（點一句換）', notes: '這一段的學習筆記', textbook: '課本寫法', examples: '同句型',
    video: '學習字幕版成片', needVideo: '影片生好、接完片之後才能燒（現在可以先下載字幕檔）。', burn: (l: string) => `燒「${l}」版`,
    burning: '燒字幕中…', download: '下載影片', srt: (l: string) => `${l} 字幕檔`, hint: '可以更口語：', edited: '已手改', say: '送給影片模型唸的字（名字換成讀音才唸得對）：',
    rubyHelp: '讀音寫法：漢字[よみ]，例如 覚[おぼ]えてる',
    review: (n: number) => n ? `製作人（Nemotron Ultra）審意思：${n} 處要改，照意見再改一版` : '製作人（Nemotron Ultra）審意思：沒有問題',
    reviewFailed: '製作人審稿沒成功（沒審過，不算通過，請自己看一遍）', dropped: (n: number) => `丟掉 ${n} 條：引用的字不在那一句裡`,
  },
  en: {
    title: 'Line director (Nemotron · study mode)', start: 'Direct the lines', restart: 'Direct again', running: 'Running', done: 'Done', failed: 'Failed',
    steps: ['Collect lines', 'Rewrite + gates', 'Study notes', 'Done'], elapsed: 'Elapsed', sec: 's', min: 'm',
    needShots: 'Direct the shot list above first — that is where the lines come from.',
    lead: (n: number, film: string) => `${n} shot(s) in this part have dialogue. Nemotron rewrites every line into spoken ${film} for who is talking to whom, adds readings and subtitles, then program gates check it (readings complete, spoken not written, fits the shot).`,
    start_: (n: number) => `Starting: ${n} line(s)`, round: (r: number, n: number, e: number) => `Version ${r}: ${n} lines, the gates caught ${e}`,
    passed: (r: number, n: number) => `Version ${r}: ${n} lines, every gate passed`, writing: (r: number) => `Nemotron is writing version ${r}`,
    finished: (n: number, notes: number, left: number, doubts = 0) => `Done: ${n} lines, ${notes} study note(s)${left ? `, ${left} issue(s) left (in red — please check)` : ''}${doubts ? `; ${doubts} reading(s) differ from the dictionary — please check (orange)` : ''}`,
    mostly: 'mostly', colWho: 'Who', colSource: 'Script line', colSpoken: 'Directed line', colWhy: 'Why it is said this way', edit: 'Edit', save: 'Save', cancel: 'Cancel',
    preview: 'Subtitle preview (click a line)', notes: 'Study notes for this part', textbook: 'Textbook form', examples: 'Same pattern',
    video: 'Study-subtitle video', needVideo: 'Available once the shots are generated and assembled (the subtitle files can be downloaded now).', burn: (l: string) => `Burn the ${l} version`,
    burning: 'Burning…', download: 'Download video', srt: (l: string) => `${l} subtitle file`, hint: 'Could be more casual: ', edited: 'edited', say: 'What the video model reads (names spelled by sound so it says them right): ',
    rubyHelp: 'Readings: 漢字[よみ], e.g. 覚[おぼ]えてる',
    review: (n: number) => n ? `Producer (Nemotron Ultra) meaning check: ${n} line(s) to fix — one more version` : 'Producer (Nemotron Ultra) meaning check: no problems',
    reviewFailed: 'The producer review failed (not reviewed — not a pass; please read it yourself)', dropped: (n: number) => `dropped ${n}: the quoted words were not in that line`,
  },
  ja: {
    title: 'セリフ演出（Nemotron・語学モード）', start: 'セリフを演出', restart: 'もう一度演出', running: '進行中', done: '完了', failed: '失敗',
    steps: ['セリフ収集', '書き直し＋関門', '学習ノート', '完了'], elapsed: '経過', sec: '秒', min: '分',
    needShots: '先に上で絵コンテを作ってください。セリフはそこから取ります。',
    lead: (n: number, film: string) => `このパートでセリフのあるカットは ${n}。Nemotron が人物関係と場面に合わせて${film}の話し言葉に書き直し、ふりがなと字幕を付け、プログラムの関門で確認します（ふりがな・口語・カット内に言い切れるか）。`,
    start_: (n: number) => `開始：セリフ ${n} 行`, round: (r: number, n: number, e: number) => `第 ${r} 版：${n} 行、関門で ${e} 件`,
    passed: (r: number, n: number) => `第 ${r} 版：${n} 行、全関門通過`, writing: (r: number) => `Nemotron が第 ${r} 版を作成中`,
    finished: (n: number, notes: number, left: number, doubts = 0) => `完了：${n} 行、学習ノート ${notes} 件${left ? `、未解決 ${left} 件（赤字・確認してください）` : ''}${doubts ? `；辞書と違う読み ${doubts} 件（橙・確認してください）` : ''}`,
    mostly: '多いのは', colWho: '誰', colSource: '台本のセリフ', colSpoken: '演出後のセリフ', colWhy: 'なぜこう言う', edit: '直す', save: '保存', cancel: '取消',
    preview: '字幕プレビュー（行をクリック）', notes: 'このパートの学習ノート', textbook: '教科書の形', examples: '同じ文型',
    video: '学習字幕版', needVideo: '映像を生成・結合した後に焼き込めます（字幕ファイルは今ダウンロードできます）。', burn: (l: string) => `${l}版を焼き込む`,
    burning: '焼き込み中…', download: '動画をダウンロード', srt: (l: string) => `${l}字幕ファイル`, hint: 'もっと口語に：', edited: '手直し済み', say: '動画モデルに読ませる文字（名前は読みに置き換え）：',
    rubyHelp: 'ふりがな：漢字[よみ]（例：覚[おぼ]えてる）',
    review: (n: number) => n ? `プロデューサー（Nemotron Ultra）意味チェック：${n} 件、指摘どおりにもう一版` : 'プロデューサー（Nemotron Ultra）意味チェック：問題なし',
    reviewFailed: 'プロデューサーの確認に失敗（未確認・合格ではありません。目を通してください）', dropped: (n: number) => `${n} 件を破棄：引用がその行にない`,
  },
}

/** 讀音標記 → <ruby>（畫面上的字幕預覽、表格都用這個） */
function Ruby({ text }: { text: string }) {
  const parts: React.ReactNode[] = []
  const re = /([一-鿿々〆ヶ]+|[ァ-ヴー]+)\[([^\]]*)\]/g
  let last = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index))
    parts.push(<ruby key={m.index}>{m[1]}<rt>{m[2]}</rt></ruby>)
    last = m.index + m[0].length
  }
  parts.push(text.slice(last))
  return <>{parts}</>
}

export function LinesPanel({ lang, projectId, order }: { lang: Lang; projectId: string; order: number }) {
  const t = T[lang]
  const [st, setSt] = useState<State | null>(null)
  const statusRef = useRef<string | null>(null)   // 最近一次的狀態：沒在跑就放慢輪詢（pollGate）
  const [err, setErr] = useState('')
  const [now, setNow] = useState(Date.now())
  const [pick, setPick] = useState(0)
  const [subLang, setSubLang] = useState<string>('')
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState<{ ruby: string; subs: Record<string, string> }>({ ruby: '', subs: {} })
  const busy = useRef(false)

  const poll = async () => {
    const r = await fetch(`/api/projects/${projectId}/segments/${order}/lines`)
    if (r.ok) { const s = await r.json(); statusRef.current = s.status; setSt(s) }
  }
  useEffect(() => {
    void poll()
    const gate = makePollGate()
    const id = setInterval(() => { if (!busy.current && gate(statusRef.current)) void poll(); setNow(Date.now()) }, 3000)
    return () => clearInterval(id)
  }, [projectId, order])
  useEffect(() => {
    if (st && !subLang && st.learner_languages.length) setSubLang(st.learner_languages[0])
  }, [st?.learner_languages.join()])

  if (!st || !st.study_mode) return null
  const film = LANG_NAME[lang][st.film_language] ?? st.film_language
  const nm = (c: string) => LANG_NAME[lang][c] ?? c
  // 伺服器回錯誤要顯示出來（不默默吞掉）；busy 一定要在 finally 放掉，不然輪詢會永遠停住
  const act = async (fn: () => Promise<void>) => {
    setErr('')
    try {
      await fn()
    } catch (e) {
      setErr((e as Error).message)
    }
    void poll()
  }
  const start = () => act(() => send(`/api/projects/${projectId}/segments/${order}/lines`, { method: 'POST' }))
  const saveEdit = (id: string) => act(async () => {
    busy.current = true
    try {
      await send(`/api/projects/${projectId}/segments/${order}/lines/${id}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(draft),
      })
      setEditing(null)
    } finally {
      busy.current = false
    }
  })
  const burn = (l: string) => act(() => send(`/api/projects/${projectId}/segments/${order}/study-video?lang=${l}`, { method: 'POST' }))

  const log = st.log
  const last = log[log.length - 1]
  const startedAt = log[0]?.t
  const elapsed = startedAt ? Math.max(0, Math.round(now / 1000 - startedAt)) : 0
  const step = !last ? -1 : last.kind === 'done' ? 3 : last.kind === 'start' ? 0 : 1
  const nowText = !last ? '' : last.kind === 'start' ? t.start_(last.lines as number)
    : last.kind === 'round_start' ? t.writing(last.round as number)
    : last.kind === 'round' ? ((last.errors as number) ? t.round(last.round as number, last.lines as number, last.errors as number) : t.passed(last.round as number, last.lines as number))
    : last.kind === 'done' ? t.finished(last.lines as number, last.notes as number, last.unresolved as number, (last.reading_doubts as number) ?? 0)
    : last.kind === 'failed' ? String(last.error) : ''
  const lines = st.lines
  const sel = lines[Math.min(pick, Math.max(0, lines.length - 1))]

  return (
    <section className="section">
      <div className="section-head">
        <h2>{t.title}</h2>
        {st.status && <span className={`badge ${st.status === 'done' ? 'done' : (st.status === 'failed' || st.status === 'interrupted') ? 'over' : 'in-progress'}`}>
          {st.status === 'done' ? t.done : (st.status === 'failed' || st.status === 'interrupted') ? t.failed : t.running}</span>}
      </div>
      {err && <div className="notice warn">{err}</div>}

      {!st.has_shot_list ? <div className="notice">{t.needShots}</div> : <p className="lang-lead">{t.lead(st.shots_with_dialogue, film)}</p>}

      {st.has_shot_list && (
        <ol className="direction-steps">
          {t.steps.map((label, i) => <li key={label} className={i < step ? 'done' : i === step ? 'current' : ''}>{label}</li>)}
        </ol>
      )}

      {last && (
        <div className={`notice ${st.status === 'failed' ? 'warn' : ''}`}>
          <strong>{nowText}</strong>
          {st.status === 'running' && <div className="direction-elapsed">{t.elapsed} {Math.floor(elapsed / 60)}{t.min} {elapsed % 60}{t.sec}</div>}
        </div>
      )}
      {log.filter((e) => e.kind === 'round' || e.kind === 'review').length > 0 && (
        <ul className="direction-log">
          {log.filter((e) => e.kind === 'round' || e.kind === 'review').map((e, i) => {
            if (e.kind === 'review') {
              const flags = (e.flags as { id: string; quote: string; problem: string }[]) ?? []
              return <li key={i} className={flags.length ? '' : 'ok'}>
                <span className="direction-at">{startedAt ? `${Math.round(e.t - startedAt)}${t.sec}` : ''}</span>
                {e.failed ? t.reviewFailed : t.review(flags.length)}
                {(e.dropped as number) > 0 && <span className="direction-warn"> · {t.dropped(e.dropped as number)}</span>}
                {flags.map((f, j) => <div key={j} className="direction-sub">{f.id}：「{f.quote}」— <AiText text={f.problem} lang={lang} projectId={projectId} /></div>)}
              </li>
            }
            const top = (e.top as { rule: string; count: number }[]) ?? []
            return <li key={i} className={(e.errors as number) === 0 ? 'ok' : ''}>
              <span className="direction-at">{startedAt ? `${Math.round(e.t - startedAt)}${t.sec}` : ''}</span>
              {(e.errors as number) === 0 ? t.passed(e.round as number, e.lines as number) : t.round(e.round as number, e.lines as number, e.errors as number)}
              {top.length > 0 && (e.errors as number) > 0 && <div className="direction-sub">
                {t.mostly}{lang === 'en' ? ': ' : '：'}{top.map((x) => `${RULE[lang][x.rule] ?? x.rule} ×${x.count}`).join(lang === 'en' ? ', ' : '、')}</div>}
            </li>
          })}
        </ul>
      )}

      {st.has_shot_list && st.status !== 'running' && (
        <div className="actions">
          <button className="btn primary" onClick={() => void start()}>{lines.length ? t.restart : t.start}</button>
        </div>
      )}

      {/* 字幕預覽：影片還沒生也看得到字幕長怎樣 */}
      {sel && subLang && (
        <>
          <div className="section-head" style={{ marginTop: 20 }}>
            <h3>{t.preview}</h3>
            <div className="chips">
              {st.learner_languages.map((l) => <button key={l} className={`btn chip ${subLang === l ? 'on' : ''}`} onClick={() => setSubLang(l)}>{nm(l)}</button>)}
            </div>
          </div>
          <div className="study-frame">
            <div className="study-sub">
              <div className="study-top"><Ruby text={sel.ruby || sel.spoken} /></div>
              <div className="study-bottom">{sel.subs[subLang] ?? ''}</div>
            </div>
          </div>
        </>
      )}

      {lines.length > 0 && (
        <div className="scroll">
          <table className="lines-table">
            <thead><tr><th>#</th><th>{t.colWho}</th><th>{t.colSource}</th><th>{t.colSpoken}</th><th>{t.colWhy}</th><th></th></tr></thead>
            <tbody>
              {lines.map((ln, i) => (
                <tr key={ln.id} className={i === pick ? 'picked' : ''} onClick={() => setPick(i)}>
                  <td className="num">{i + 1}</td>
                  <td className="key">{ln.speaker}<div><span className="badge reg">{REG[lang][ln.register] ?? ln.register}</span></div></td>
                  <td style={{ color: 'var(--light-dim)' }}>{ln.source}</td>
                  <td>
                    {editing === ln.id ? (
                      <div className="line-edit" onClick={(e) => e.stopPropagation()}>
                        <textarea value={draft.ruby} onChange={(e) => setDraft({ ...draft, ruby: e.target.value })} rows={2} />
                        <div className="section-note">{t.rubyHelp}</div>
                        {st.learner_languages.map((l) => (
                          <label key={l}>{nm(l)}
                            <input value={draft.subs[l] ?? ''} onChange={(e) => setDraft({ ...draft, subs: { ...draft.subs, [l]: e.target.value } })} />
                          </label>
                        ))}
                        <div className="actions">
                          <button className="btn primary small" onClick={() => void saveEdit(ln.id)}>{t.save}</button>
                          <button className="btn ghost small" onClick={() => setEditing(null)}>{t.cancel}</button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <div className="line-spoken"><Ruby text={ln.ruby || ln.spoken} /></div>
                        {st.learner_languages.map((l) => <div key={l} className="line-sub">{nm(l)}：{ln.subs[l] ?? '—'}</div>)}
                        {ln.issues.map((x, j) => <div key={j} className={x.level === 'ERROR' ? 'direction-warn' : x.rule === 'reading' ? 'reading-doubt' : 'section-note'}>
                          {RULE[lang][x.rule] ?? x.rule}{lang === 'zh' ? `：${x.zh}` : ''}</div>)}
                        {ln.hints.length > 0 && <div className="section-note">{t.hint}{ln.hints.join('、')}</div>}
                        {ln.say && ln.say !== ln.spoken && <div className="section-note">{t.say}{ln.say}</div>}
                        {ln.edited && <span className="section-note">{t.edited}</span>}
                      </>
                    )}
                  </td>
                  <td>{ln.why && <AiText text={ln.why} lang={lang} projectId={projectId} />}</td>
                  <td>
                    {editing !== ln.id && <button className="btn ghost small" onClick={(e) => {
                      e.stopPropagation(); setEditing(ln.id); setDraft({ ruby: ln.ruby || ln.spoken, subs: { ...ln.subs } })
                    }}>{t.edit}</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {st.notes.length > 0 && (
        <>
          <h3 style={{ marginTop: 24 }}>{t.notes}</h3>
          <div className="study-notes">
            {st.notes.map((n, i) => {
              const ln = lines.find((x) => x.id === n.line_id)
              return (
                <div className="study-note" key={i}>
                  <div className="study-note-point"><AiText text={n.point} lang={lang} projectId={projectId} /></div>
                  {ln && <div className="line-spoken"><Ruby text={ln.ruby || ln.spoken} /></div>}
                  <p><AiText text={n.explain} lang={lang} projectId={projectId} /></p>
                  <div className="section-note">{t.textbook}{lang === 'en' ? ': ' : '：'}<Ruby text={n.textbook} /></div>
                  <div className="section-note">{t.examples}{lang === 'en' ? ':' : '：'}</div>
                  <ul>{n.examples.map((x, j) => {
                    // 例句「日文 — 學習者語言的意思」：日文照原樣，意思跟介面語言（英文介面整段中文）
                    const [ja, gloss] = x.split(' — ')
                    return <li key={j}><Ruby text={ja} />{gloss && <> — <AiText text={gloss} lang={lang} projectId={projectId} /></>}</li>
                  })}</ul>
                </div>
              )
            })}
          </div>
        </>
      )}

      {lines.length > 0 && (
        <>
          <h3 style={{ marginTop: 24 }}>{t.video}</h3>
          {!st.has_video && <div className="notice">{t.needVideo}</div>}
          <div className="actions">
            {st.learner_languages.map((l) => {
              const v = st.study_videos[l]
              const pair = `${film}＋${nm(l)}`
              return (
                <span key={l} className="study-video-row">
                  <a className="btn ghost small" href={`/api/projects/${projectId}/segments/${order}/study-subs.srt?lang=${l}`}>{t.srt(pair)}</a>
                  {st.has_video && (v?.running || v?.status === 'running'
                    ? <span className="section-note">{t.burning}</span>
                    : <button className="btn primary small" onClick={() => void burn(l)}>{t.burn(pair)}</button>)}
                  {v?.status === 'done' && <a className="btn ghost small" href={`/api/projects/${projectId}/segments/${order}/study-video/${l}`}>{t.download}</a>}
                  {v?.status === 'failed' && <span className="direction-warn">{v.error}</span>}
                </span>
              )
            })}
          </div>
        </>
      )}
    </section>
  )
}
