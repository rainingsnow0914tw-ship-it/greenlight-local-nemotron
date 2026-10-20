/**
 * 導演進度（Greenlight Local）：場面調度 → 導演迴圈一回合一回合 → 製作人評 → 完成。
 *
 * 為什麼要有：一場戲導演要 7～15 分鐘。畫面不講話的話，使用者只看到一個轉圈圈，不知道程式在幹嘛
 * （使用者要看得出來 UI 在跑什麼、做到哪一步）。
 * 所以每一步都用人話說出來：關卡擋了什麼（不是規則編號）、改了幾鏡、製作人說了什麼、丟掉幾條沒根據的意見。
 *
 * 資料來源：GET /api/projects/{id}/segments/{order}/direct（後端每一步都寫進 direction_log），每 3 秒輪詢。
 */
import { useEffect, useRef, useState } from 'react'
import { makePollGate } from '../pollGate'
import { AiText, Gloss } from './AiText'
import { FixShotButton, GateMessage } from './FixShotButton'
import type { Lang } from '../api'

type LogEntry = { t: number; kind: string; [k: string]: unknown }
type State = { status: string | null; log: LogEntry[]; total: number; shots: number; scenes: Record<string, any> }

const RULES: Record<Lang, Record<string, string>> = {
  zh: {
    'rule 1': '寫了「不要／沒有」的禁令句', 'rule 3': '寫了會讓角色出聲的字', 'rule 4': '開口前只有臉部小動作',
    'rule 6': '講話的人不在畫面裡', 'rule 7': '一鏡超過 10 秒', 'rule 8': '秒數不夠講完台詞', 'rule 9': '台詞跟劇本對不上',
    'rule 10': '不同世界的人同框', 'rule 15': '沒選站位', 'rule 16': '要沉默又要講台詞', 'rule 17': '邊打字邊講話',
    'rule 18': '道具跑到別的世界', 'rule 21': '兩人同框沒講誰做什麼', 'rule 22': '景別單調或鏡頭從不動',
    'rule 23': '同一句動作抄了三鏡以上', 'rule 24': '台詞拆在句子中間', 'rule 25': '比手畫腳', 'rule 26': '對話一直你一鏡我一鏡', 'rule 27': '接上一鏡時景別跳太多或少了人', 'rule 28': '字面陷阱（眼裡、淚珠、短台詞…）', 'rule 29': '動作沒有開始和結束', 'rule 30': '沒首幀卻沒寫站位', 'rule 31': '室內往後拉遠', 'no first frame': '沒首幀做不到的運鏡或動作', enums: '欄位值不合法',
    ids: '鏡號重複', format: '回覆格式壞掉', cast: '用了不在場的角色',
  },
  en: {
    'rule 1': 'wrote a "don\'t / no" instruction', 'rule 3': 'words that make the character make a sound',
    'rule 4': 'only face movements before speaking', 'rule 6': 'the speaker is not in frame', 'rule 7': 'a shot over 10 s',
    'rule 8': 'too short to finish the line', 'rule 9': 'a line differs from the script', 'rule 10': 'two worlds in one frame',
    'rule 15': 'no spot chosen', 'rule 16': 'silent and speaking at once', 'rule 17': 'typing while speaking',
    'rule 18': 'a prop in the wrong world', 'rule 21': 'two people, unclear who does what', 'rule 22': 'monotonous framing or a camera that never moves',
    'rule 23': 'the same action copied into 3+ shots', 'rule 24': 'a line split mid-sentence', 'rule 25': 'miming', 'rule 26': 'face-by-face cuts for every line',
    'rule 27': 'chained into a big size jump or a missing person', 'rule 28': 'a literal trap (poetic eyes, tear drops, very short line…)', 'rule 29': 'an action with no start and end', 'rule 30': 'no first frame and no starting position', 'rule 31': 'pulling back indoors', 'no first frame': 'a move or action impossible without a first frame',
    enums: 'invalid field value', ids: 'duplicate shot id', format: 'malformed reply', cast: 'a character not in the scene',
  },
  ja: {
    'rule 1': '「〜しない」の禁止文', 'rule 3': 'キャラが声を出してしまう語', 'rule 4': '話す前が顔の小さな動きだけ',
    'rule 6': '話し手が画面にいない', 'rule 7': '1カット10秒超', 'rule 8': 'セリフに対して秒数不足', 'rule 9': 'セリフが台本と違う',
    'rule 10': '別世界の人物が同じ画面に', 'rule 15': '立ち位置未選択', 'rule 16': '黙るのに話す', 'rule 17': 'タイピングしながら話す',
    'rule 18': '小道具が別世界に', 'rule 21': '二人いるのに誰が何をするか不明', 'rule 22': '景別が単調・カメラが動かない',
    'rule 23': '同じ動作を3カット以上に', 'rule 24': 'セリフを文の途中で分割', 'rule 25': '身振り手振り', 'rule 26': 'セリフごとに一人ずつ切り返し', 'rule 27': 'つなぎで景別が飛ぶ・人物が足りない', 'rule 28': '字面のわな（目の中、涙の粒、短すぎるセリフ…）', 'rule 29': '動作に始まりと終わりがない', 'rule 30': '最初のフレームがないのに立ち位置がない', 'rule 31': '室内で引く', 'no first frame': '最初のフレームなしではできない動き', enums: '不正な値',
    ids: 'カット番号の重複', format: '返答の形式が壊れた', cast: '場にいない人物',
  },
}

const T = {
  zh: {
    title: '導演分鏡（Nemotron）', start: '開始導演', restart: '重新導演', running: '進行中', done: '完成', failed: '失敗', interrupted: '中斷了',
    steps: ['場面調度', '寫分鏡＋關卡', '製作人評', '完成'], elapsed: '已經', sec: '秒', min: '分',
    now: {
      start: (n: number) => `開始：這一段有 ${n} 場戲`,
      staging: (s: number) => `第 ${s} 場：Nemotron 正在整理場面調度（世界、站位、道具、台詞）`,
      staged: (s: number) => `第 ${s} 場：調度好了，開始寫分鏡`,
      round: (s: number, r: number) => `第 ${s} 場：Nemotron 正在改第 ${r + 1} 版`,
      critique: (s: number) => `第 ${s} 場：照製作人的意見改`,
      scene_done: (s: number) => `第 ${s} 場：導完了`,
      done: () => '全部導完，分鏡表已經準備好', failed: () => '停下來了',
    },
    staged: (spots: number, chars: string, lines: number) => `站位 ${spots} 個 · 角色 ${chars} · 台詞 ${lines} 句`,
    unverified: (n: number) => `${n} 句台詞跟劇本對不上，請檢查`,
    round: (r: number, shots: number, errors: number) => `第 ${r} 回合：${shots} 鏡，關卡擋下 ${errors} 處`,
    passed: (r: number, shots: number) => `第 ${r} 回合：${shots} 鏡，關卡全過`,
    rejected: '改壞了，不收，退回上一版', topRules: '最多的是',
    critique: (v: string, n: number) => `製作人（Nemotron Ultra）：${v === 'approve' ? '通過' : v === 'revise' ? '要改' : v}，${n} 條意見`,
    finalCut: '審的是人手修完之後的最終分鏡',
    rework: (n: number) => `人手照這場戲已發布的正式版分鏡重排成 ${n} 鏡（同框、誰講台詞看衣服、情緒寫進動作），台詞用台詞導演導過的版本`,
    dropped: (n: number) => `丟掉 ${n} 條：它引用的句子不在它點名的那一鏡`,
    sceneDone: (shots: number, status: string) => `${shots} 鏡 · ${status === 'passed' ? '關卡全過' : status === 'passed_unreviewed' ? '關卡全過（製作人沒評成）' : '沒在回合內過關，交給你看'}`,
    finished: (shots: number, secs: number) => `完成：${shots} 鏡、${secs} 秒`,
    twoShots: (n: number, pairs: string) => `一來一往的 ${n} 組正反打合成兩人同框（${pairs}），合完重跑關卡`,
  },
  en: {
    title: 'Shot list by the director (Nemotron)', start: 'Start directing', restart: 'Direct again', running: 'Running', done: 'Done', failed: 'Failed', interrupted: 'Interrupted',
    steps: ['Staging', 'Shots + gates', 'Producer review', 'Done'], elapsed: 'Elapsed', sec: 's', min: 'm',
    now: {
      start: (n: number) => `Starting: ${n} scene(s) in this part`,
      staging: (s: number) => `Scene ${s}: Nemotron is staging it (worlds, spots, props, lines)`,
      staged: (s: number) => `Scene ${s}: staged — writing the shot list`,
      round: (s: number, r: number) => `Scene ${s}: Nemotron is writing version ${r + 1}`,
      critique: (s: number) => `Scene ${s}: revising to the producer's notes`,
      scene_done: (s: number) => `Scene ${s}: directed`,
      done: () => 'All directed — the shot list is ready', failed: () => 'Stopped',
    },
    staged: (spots: number, chars: string, lines: number) => `${spots} spot(s) · ${chars} · ${lines} line(s)`,
    unverified: (n: number) => `${n} line(s) don't match the script — please check`,
    round: (r: number, shots: number, errors: number) => `Round ${r}: ${shots} shots, the gates caught ${errors}`,
    passed: (r: number, shots: number) => `Round ${r}: ${shots} shots, every gate passed`,
    rejected: 'made it worse — not applied, kept the previous version', topRules: 'mostly',
    critique: (v: string, n: number) => `Producer (Nemotron Ultra): ${v === 'approve' ? 'approved' : v === 'revise' ? 'revise' : v}, ${n} note(s)`,
    finalCut: 'reviewing the final shot list after the human fixes',
    rework: (n: number) => `Reworked by hand into ${n} shots following the published, human-directed storyboard of this scene (two-shots, speakers named by their clothes, emotion written as actions); lines from the line director`,
    dropped: (n: number) => `dropped ${n}: the quoted words were not in the shots it named`,
    sceneDone: (shots: number, status: string) => `${shots} shots · ${status === 'passed' ? 'every gate passed' : status === 'passed_unreviewed' ? 'gates passed (review failed)' : 'did not pass in time — over to you'}`,
    finished: (shots: number, secs: number) => `Done: ${shots} shots, ${secs} s`,
    twoShots: (n: number, pairs: string) => `Merged ${n} back-and-forth pair(s) into two-shots (${pairs}), gates re-run after each`,
  },
  ja: {
    title: '監督による絵コンテ（Nemotron）', start: '演出を始める', restart: 'もう一度演出', running: '進行中', done: '完了', failed: '失敗', interrupted: '中断',
    steps: ['段取り', '絵コンテ＋関門', 'プロデューサー', '完了'], elapsed: '経過', sec: '秒', min: '分',
    now: {
      start: (n: number) => `開始：このパートは ${n} シーン`,
      staging: (s: number) => `シーン ${s}：Nemotron が段取りを整理中（世界・立ち位置・小道具・セリフ）`,
      staged: (s: number) => `シーン ${s}：段取り完了、絵コンテ作成へ`,
      round: (s: number, r: number) => `シーン ${s}：Nemotron が第 ${r + 1} 版を作成中`,
      critique: (s: number) => `シーン ${s}：プロデューサーの指摘で修正中`,
      scene_done: (s: number) => `シーン ${s}：完了`,
      done: () => '全シーン完了、絵コンテの準備ができました', failed: () => '停止しました',
    },
    staged: (spots: number, chars: string, lines: number) => `立ち位置 ${spots} · ${chars} · セリフ ${lines}`,
    unverified: (n: number) => `${n} 行が台本と一致しません。確認してください`,
    round: (r: number, shots: number, errors: number) => `第 ${r} ラウンド：${shots} カット、関門で ${errors} 件`,
    passed: (r: number, shots: number) => `第 ${r} ラウンド：${shots} カット、全関門通過`,
    rejected: '悪化したので不採用、前の版に戻す', topRules: '多いのは',
    critique: (v: string, n: number) => `プロデューサー（Nemotron Ultra）：${v === 'approve' ? '承認' : v === 'revise' ? '要修正' : v}、指摘 ${n} 件`,
    finalCut: '人が手直しした後の最終絵コンテを審査',
    rework: (n: number) => `このシーンの公開済み本番絵コンテに沿って人が ${n} カットに組み直し（二人を同じ画面に、話し手は服で指定、感情は動作で）、セリフはセリフ演出の版`,
    dropped: (n: number) => `${n} 件を破棄：引用がそのカットに存在しない`,
    sceneDone: (shots: number, status: string) => `${shots} カット · ${status === 'passed' ? '全関門通過' : status === 'passed_unreviewed' ? '関門通過（講評失敗）' : '時間内に通らず、確認をお願いします'}`,
    finished: (shots: number, secs: number) => `完了：${shots} カット、${secs} 秒`,
    twoShots: (n: number, pairs: string) => `切り返し ${n} 組を二人同じ画面にまとめた（${pairs}）、まとめるたびにゲートを再確認`,
  },
}

const GATE: Record<Lang, { title: (n: number) => string; note: string }> = {
  zh: { title: (n) => `${n} 鏡生成時會被生成前關卡擋下`, note: '現在就可以讓 Nemotron 修這幾鏡（只改這一鏡，整場變糟就不收）；不修也能先生其他鏡，一鍵生成會先跳過它們。' },
  en: { title: (n) => `${n} shot(s) will be blocked by the pre-render gate`, note: 'Let Nemotron fix them now (only that shot changes; the fix is dropped if the scene gets worse). You can also generate the rest first — one-click generation skips them.' },
  ja: { title: (n) => `${n} カットが生成前チェックで止まります`, note: '今すぐ Nemotron に直させられます（そのカットだけ変更、シーン全体が悪くなれば採用しません）。直さずに他を先に生成することもできます —— 一括生成はこれらを飛ばします。' },
}

export function DirectionPanel({ lang, projectId, order, onDone }: {
  lang: Lang; projectId: string; order: number; onDone: () => void
}) {
  const t = T[lang]
  const [st, setSt] = useState<State | null>(null)
  const statusRef = useRef<string | null>(null)   // 最近一次的狀態：沒在跑就放慢輪詢（pollGate）
  const [now, setNow] = useState(Date.now())
  const finished = useRef(false)
  // 生成時會被生成前關卡擋的鏡：導演一跑完就列出來＋修鏡按鈕（範例 3：「交給你看」卻沒地方修，要等生成被擋才出現）
  const [gate, setGate] = useState<{ shot_id: string; error: string }[]>([])
  const [fixNote, setFixNote] = useState('')
  const loadGate = async () => {
    const r = await fetch(`/api/projects/${projectId}/segments/${order}/gate-issues`)
    if (r.ok) setGate((await r.json()).issues ?? [])
  }

  const poll = async () => {
    const r = await fetch(`/api/projects/${projectId}/segments/${order}/direct`)
    if (r.ok) { const s = await r.json(); statusRef.current = s.status; setSt(s) }
  }
  useEffect(() => {
    void poll()
    const gate = makePollGate()
    const id = setInterval(() => { if (gate(statusRef.current)) void poll(); setNow(Date.now()) }, 3000)
    return () => clearInterval(id)
  }, [projectId, order])
  useEffect(() => {
    if (st?.status === 'done' && !finished.current) {
      finished.current = true
      onDone()
    }
    if (st?.status === 'done') void loadGate()
  }, [st?.status])

  const start = async (force: boolean) => {
    finished.current = false
    await fetch(`/api/projects/${projectId}/segments/${order}/direct${force ? '?force=true' : ''}`, { method: 'POST' })
    void poll()
  }

  const log = st?.log ?? []
  const last = log[log.length - 1]
  const startedAt = log[0]?.t
  const elapsed = startedAt ? Math.max(0, Math.round(now / 1000 - startedAt)) : 0
  const step = !last ? -1 : last.kind === 'done' ? 3 : last.kind === 'critique' ? 2
    : last.kind === 'staging' || last.kind === 'start' ? 0 : 1
  const scene = (last?.scene as number) ?? 1
  const nowText = !last ? '' : last.kind === 'start' ? t.now.start(last.scenes as number)
    : last.kind === 'round' ? t.now.round(scene, last.round as number)
    : (t.now as any)[last.kind]?.(scene) ?? ''
  const rule = (r: string) => RULES[lang][r] ?? r

  return (
    <section className="section">
      <div className="section-head">
        <h2>{t.title}</h2>
        {st?.status && <span className={`badge ${st.status === 'done' ? 'done' : (st.status === 'failed' || st.status === 'interrupted') ? 'over' : 'in-progress'}`}>
          {st.status === 'done' ? t.done : st.status === 'failed' ? t.failed : st.status === 'interrupted' ? t.interrupted : t.running}</span>}
      </div>

      {/* 走到哪一步 */}
      <ol className="direction-steps">
        {t.steps.map((label, i) => (
          <li key={label} className={i < step ? 'done' : i === step ? 'current' : ''}>{label}</li>
        ))}
      </ol>

      {/* 現在在做什麼 */}
      {last && (
        <div className={`notice ${st?.status === 'failed' ? 'warn' : ''}`}>
          <strong>{nowText}</strong>
          {st?.status === 'running' && <div className="direction-elapsed">
            {t.elapsed} {Math.floor(elapsed / 60)}{t.min} {elapsed % 60}{t.sec}</div>}
          {last.kind === 'failed' && <div className="direction-elapsed">{String(last.error)}</div>}
        </div>
      )}

      {/* 一路的紀錄 */}
      {log.length > 0 && (
        <ul className="direction-log">
          {log.map((e, i) => {
            const at = startedAt ? `${Math.round(e.t - startedAt)}${t.sec}` : ''
            if (e.kind === 'staged') {
              const chars = (e.characters as string[]).join('、')
              return <li key={i}><span className="direction-at">{at}</span>
                {t.staged((e.spots as string[]).length, chars, e.lines as number)}
                {(e.unverified as number) > 0 && <span className="direction-warn"> · {t.unverified(e.unverified as number)}</span>}</li>
            }
            if (e.kind === 'round') {
              const top = (e.top as { rule: string; count: number }[]) ?? []
              return <li key={i} className={(e.errors as number) === 0 ? 'ok' : e.rejected ? 'bad' : ''}>
                <span className="direction-at">{at}</span>
                {/* 長場戲分段同時導：不標第幾段，各段的「第 3 回合」混在一起分不出來 */}
                {(e.parts as number) > 1 && <span className="direction-at">
                  {lang === 'en' ? `part ${e.part}/${e.parts} ` : lang === 'ja' ? `${e.part}/${e.parts}部 ` : `第 ${e.part}/${e.parts} 段 `}</span>}
                {(e.errors as number) === 0 ? t.passed(e.round as number, e.shots as number) : t.round(e.round as number, e.shots as number, e.errors as number)}
                {Boolean(e.rejected) && <span className="direction-warn"> · {t.rejected}</span>}
                {top.length > 0 && (e.errors as number) > 0 && <div className="direction-sub">
                  {t.topRules}{lang === 'en' ? ': ' : '：'}{top.map((x) => `${rule(x.rule)} ×${x.count}`).join(lang === 'en' ? ', ' : '、')}</div>}
              </li>
            }
            if (e.kind === 'critique') {
              const notes = (e.notes as any[]) ?? []
              return <li key={i}><span className="direction-at">{at}</span>
                {t.critique(String(e.verdict), notes.length)}
                {Boolean(e.final_cut) && <span className="direction-warn"> · {t.finalCut}</span>}
                {(e.dropped as number) > 0 && <span className="direction-warn"> · {t.dropped(e.dropped as number)}</span>}
                {notes.map((n, j) => <div key={j} className="direction-sub">
                  {/* 整體意見沒有鏡號、沒有引文：不印空的「：「」」（範例 2） */}
                  {(n.shots ?? []).length > 0 && <>{(n.shots ?? []).join(lang === 'en' ? ', ' : '、')}{lang === 'en' ? ': ' : '：'}</>}
                  {n.quote && <>「{n.quote}」<Gloss text={n.quote} lang={lang} projectId={projectId} />— </>}
                  <AiText text={n.problem} lang={lang} projectId={projectId} /></div>)}
              </li>
            }
            if (e.kind === 'scene_done') return <li key={i} className="ok"><span className="direction-at">{at}</span>
              {t.now.scene_done(e.scene as number)} · {t.sceneDone(e.shots as number, String(e.status))}</li>
            if (e.kind === 'human_rework') return <li key={i} className="ok"><span className="direction-at">{at}</span>
              {t.rework(e.shots as number)}</li>
            if (e.kind === 'two_shots') {
              const pairs = (e.pairs as string[]) ?? []
              return <li key={i} className="ok"><span className="direction-at">{at}</span>
                {t.twoShots(pairs.length, pairs.join(lang === 'en' ? ', ' : '、'))}</li>
            }
            if (e.kind === 'done') return <li key={i} className="ok"><span className="direction-at">{at}</span>
              {t.finished(e.shots as number, e.seconds as number)}</li>
            return null
          })}
        </ul>
      )}

      {st?.status === 'done' && gate.length > 0 && (
        <div className="notice warn">
          <strong>{GATE[lang].title(gate.length)}</strong>
          <div className="section-note">{GATE[lang].note}</div>
          {gate.map((g) => (
            <div key={g.shot_id} style={{ marginTop: 10 }}>
              <div style={{ fontFamily: 'var(--mono)', fontSize: 12 }}><GateMessage error={g.error} lang={lang} projectId={projectId} /></div>
              <FixShotButton projectId={projectId} order={order} error={g.error} lang={lang}
                onFixed={(note) => { setFixNote(note); void loadGate(); onDone() }} />
            </div>
          ))}
        </div>
      )}
      {fixNote && <div className="notice">{fixNote}</div>}

      <div className="actions">
        {st?.status !== 'running' && (
          <button className="btn primary" onClick={() => void start(st?.status === 'done' || (st?.shots ?? 0) > 0)}>
            {st?.status ? t.restart : t.start}
          </button>
        )}
      </div>
    </section>
  )
}
