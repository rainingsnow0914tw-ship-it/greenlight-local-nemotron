/**
 * 選場景：準備參考卡（人物全身卡＋每個地點四張空景：正打／反打 × 遠／中），每張可以重生或換成自己的照片。
 * 後端 routes/direction.py 的 /references；空景的寫法在 services/ref_images.plate_views。
 *
 * 為什麼要有這一頁：之前前端沒有任何按鈕會叫「準備參考卡」，一般使用者照正常流程走拿不到空景，
 * 每一鏡只能靠文字猜場景（第 61 場沒首幀的鏡跑到戶外、長出百葉窗）。
 */
import { useEffect, useRef, useState } from 'react'
import { makePollGate } from '../pollGate'
import { send, type Lang } from '../api'
import { AiText } from './AiText'

type Plate = { scene: string; name: string; location: string; place: string; view: string; faces_en: string; file: string; own?: boolean }
type LogEntry = { t: number; kind: string; [k: string]: unknown }
type Card = { card: string; name: string; file: string; face_file: string }
type State = { status: string | null; log: LogEntry[]; plates?: Plate[]; cards?: Card[]; similar?: LogEntry[]; people?: LogEntry[] }

const T = {
  zh: {
    cardsTitle: '人物卡（同一個人每一場都用這組）', cardNote: '想改哪裡（可不填，例如「保留金屬盔甲」）', faceCard: '臉', bodyCard: '全身',
    needs: (n: number, locs: number, have: number) => `這一段需要 ${locs} 個地點 × 4 張空景（共 ${n} 張，已經有 ${have} 張）。三種做法都可以：先上傳自己的圖（原圖照用）；沒上傳的按「準備參考卡」由模型生；什麼都不給也能拍，只是每一鏡的長相和場景可能不一致。人物卡在選角頁，可以「直接用這張」自己的圖。`, empty: '還沒有', ownTag: '你上傳的',
    title: '選場景：空景卡與人物卡', start: '準備參考卡', restart: '重新準備參考卡', cardsOnly: '只準備人物卡（空景用文字描述）', running: '準備中…', done: '好了',
    lead: '每個地點生四張空景：正打、反打（轉 180 度看對面那面牆）× 遠空景（全景、中景用）、中空景（中近景、特寫用）。每一鏡會自動配：誰正面對鏡頭，就用他背後那面牆；接上一鏡的就跟上一鏡同一面。不滿意可以重生，或換成你自己拍的照片（不要有人）。',
    view: { A: '正打・遠', A_near: '正打・中', B: '反打・遠', B_near: '反打・中' } as Record<string, string>,
    regen: '重生', upload: '換成我的照片', note: '想改哪裡（可不填，例如「門在左邊」）', similar: (loc: string, s: unknown) => `${loc}：正打跟反打兩張太像（相似度 ${s}），可能是同一面牆 —— 重生反打那張`,
    log: { plate_plan: (e: LogEntry) => `${e.location}：規劃空景`, plate_done: (e: LogEntry) => `${e.location}：${e.view} 完成（${e.seconds} 秒）`, views: (e: LogEntry) => `${e.location}：每一鏡配好了（正打 ${e.a} 鏡、反打 ${e.b} 鏡）`, card_skipped: (e: LogEntry) => `${e.character}：沒有選角，跳過全身卡（要它出現在畫面上，先去選角）`, done: () => '參考卡都好了', failed: (e: LogEntry) => `停下來了：${e.error}` } as Record<string, (e: LogEntry) => string>,
    people: (loc: string, v: string) => `${loc}・${v}：重生三次都還看得到人 —— 換一張你自己的照片，或寫一句要改的再重生。位置：`,
    sample: '這是內建範例，只能看。',
  },
  en: {
    cardsTitle: 'Character cards (each person uses this set in every scene)', cardNote: 'What to change (optional, e.g. “keep the metal armour”)', faceCard: 'Face', bodyCard: 'Full body',
    needs: (n: number, locs: number, have: number) => `This part needs ${locs} location(s) × 4 empty sets (${n} pictures, ${have} ready). Any of three ways works: upload your own (used as is); press “Prepare reference cards” and the model makes whatever is missing; or give nothing and shoot from text alone, though faces and places may drift between shots. Character cards are on the casting page, where you can “Use this image as is”.`, empty: 'Not yet', ownTag: 'Your upload',
    title: 'Sets: empty-set cards and character cards', start: 'Prepare reference cards', restart: 'Prepare the cards again', cardsOnly: 'Character cards only (sets described in text)', running: 'Preparing…', done: 'Ready',
    lead: 'Each location gets four empty-set plates: the main side and the reverse (the camera turned 180° to the opposite wall) × wide (for wide and medium shots) and medium (for medium close-ups and close-ups). Every shot is matched automatically: whoever faces the camera gets the wall behind them; a shot that continues the previous one keeps its side. Regenerate any you dislike, or replace it with your own photo (no people in it).',
    view: { A: 'Main · wide', A_near: 'Main · medium', B: 'Reverse · wide', B_near: 'Reverse · medium' } as Record<string, string>,
    regen: 'Regenerate', upload: 'Use my photo', note: 'What to change (optional, e.g. "door on the left")', similar: (loc: string, s: unknown) => `${loc}: the main and reverse plates look too alike (similarity ${s}) — probably the same wall; regenerate the reverse one`,
    log: { plate_plan: (e: LogEntry) => `${e.location}: planning the sets`, plate_done: (e: LogEntry) => `${e.location}: ${e.view} done (${e.seconds}s)`, views: (e: LogEntry) => `${e.location}: shots matched (main ${e.a}, reverse ${e.b})`, card_skipped: (e: LogEntry) => `${e.character}: not cast — no full-body card (cast it first if it should appear on screen)`, done: () => 'All reference cards ready', failed: (e: LogEntry) => `Stopped: ${e.error}` } as Record<string, (e: LogEntry) => string>,
    people: (loc: string, v: string) => `${loc} · ${v}: a person is still visible after three tries — use your own photo, or add a note and regenerate. Where: `,
    sample: 'This is the built-in sample — view only.',
  },
  ja: {
    cardsTitle: 'キャラクターカード（同じ人物は全シーンでこの組を使う）', cardNote: '直したいところ（任意、例「金属の鎧はそのまま」）', faceCard: '顔', bodyCard: '全身',
    needs: (n: number, locs: number, have: number) => `このパートに必要なのは ${locs} か所 × 空舞台4枚（計 ${n} 枚、用意済み ${have} 枚）。3つのやり方どれでもよい：自分の画像をアップロード（そのまま使用）／未アップロードの分は「参照カードを用意」でモデルが生成／何も渡さずテキストだけでも撮れる（ただし顔や場所がカットごとにぶれることがある）。キャラクターカードはキャスティング画面の「この画像をそのまま使う」で。`, empty: 'まだない', ownTag: 'アップロード済み',
    title: '場所選び：空舞台カードと人物カード', start: '参照カードを用意する', restart: '参照カードを作り直す', cardsOnly: '人物カードだけ用意（背景は文章で）', running: '用意中…', done: '完了',
    lead: '場所ごとに空舞台を4枚作る：正面と切り返し（カメラを180度回して向かいの壁）× 引き（ロング・ミドル用）と寄り（バスト・アップ用）。各カットは自動で合わせる：カメラの正面にいる人の背後の壁を使い、前のカットから続くカットは同じ側を使う。気に入らなければ作り直すか、自分で撮った写真（人が写っていないもの）に替えられる。',
    view: { A: '正面・引き', A_near: '正面・寄り', B: '切り返し・引き', B_near: '切り返し・寄り' } as Record<string, string>,
    regen: '作り直す', upload: '自分の写真に替える', note: '直したいところ（任意、例：「ドアは左」）', similar: (loc: string, s: unknown) => `${loc}：正面と切り返しが似すぎている（類似度 ${s}）—— 同じ壁かもしれない。切り返しを作り直して`,
    log: { plate_plan: (e: LogEntry) => `${e.location}：空舞台を計画`, plate_done: (e: LogEntry) => `${e.location}：${e.view} 完了（${e.seconds} 秒）`, views: (e: LogEntry) => `${e.location}：カットの割り当て完了（正面 ${e.a}、切り返し ${e.b}）`, card_skipped: (e: LogEntry) => `${e.character}：キャスト未決定のため全身カードを省略（画面に出すなら先にキャスティング）`, done: () => '参照カードがそろった', failed: (e: LogEntry) => `停止：${e.error}` } as Record<string, (e: LogEntry) => string>,
    people: (loc: string, v: string) => `${loc}・${v}：3 回作っても人が写っている —— 自分の写真に替えるか、直したいところを書いて作り直して。場所：`,
    sample: '内蔵サンプルです。閲覧のみ。',
  },
}

const ORDER = ['A', 'A_near', 'B', 'B_near']

export function ScenePanel({ lang, projectId, order }: { lang: Lang; projectId: string; order: number }) {
  const t = T[lang]
  const [st, setSt] = useState<State | null>(null)
  const statusRef = useRef<string | null>(null)   // 最近一次的狀態：沒在跑就放慢輪詢（pollGate）
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [note, setNote] = useState<Record<string, string>>({})
  const [bump, setBump] = useState(0)   // 換過圖就讓 <img> 重新抓
  const alive = useRef(true)
  const base = `/api/projects/${projectId}/segments/${order}/references`
  const sample = projectId.startsWith('p-sample-')
  const poll = async () => {
    const r = await fetch(base)
    if (r.ok && alive.current) { const s = await r.json(); statusRef.current = s.status; setSt(s) }
  }
  useEffect(() => {
    alive.current = true
    void poll()
    const gate = makePollGate()
    const id = setInterval(() => { if (gate(statusRef.current)) void poll() }, 3000)
    return () => { alive.current = false; clearInterval(id) }
  }, [projectId, order])
  if (!st) return null
  const act = async (key: string, fn: () => Promise<void>) => {
    setErr('')
    setBusy(key)
    try { await fn() } catch (e) { setErr((e as Error).message) }
    setBusy(null)
    setBump((b) => b + 1)
    void poll()
  }
  const running = st.status === 'running'
  const plates = st.plates ?? []
  const groups = [...new Set(plates.map((p) => `${p.scene}|${p.location}`))]
  const last = st.log[st.log.length - 1]

  return (
    <section className="section">
      <div className="section-head">
        <h2>{t.title}</h2>
        {st.status && <span className={`badge ${st.status === 'done' ? 'done' : st.status === 'failed' ? 'over' : 'in-progress'}`}>
          {st.status === 'done' ? t.done : running ? t.running : st.status}</span>}
      </div>
      {err && <div className="notice warn">{err}</div>}
      <p className="lang-lead">{t.lead}</p>
      {plates.length > 0 && (
        <div className="notice">{t.needs(plates.length, new Set(plates.map((p) => `${p.scene}|${p.location}`)).size, plates.filter((p) => p.file).length)}</div>
      )}
      {(st.similar ?? []).map((e, i) => <div key={i} className="notice warn">{t.similar(String(e.location), e.similarity)}</div>)}
      {(st.people ?? []).map((e, i) => <div key={`p${i}`} className="notice warn">{t.people(String(e.location), t.view[String(e.view)] ?? String(e.view))}<AiText text={String(e.where)} lang={lang} projectId={projectId} /></div>)}
      {running && last && t.log[last.kind] && <p className="section-note">{t.log[last.kind](last)}</p>}
      {(st.cards ?? []).length > 0 && <h3>{t.cardsTitle}</h3>}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 12 }}>
        {(st.cards ?? []).map((c) => (
          <figure key={c.card} style={{ margin: 0 }}>
            <div style={{ display: 'flex', gap: 6 }}>
              <img src={`/api/projects/${projectId}/media/${c.file}/raw?b=${bump}`} alt={`${c.name} ${t.bodyCard}`} style={{ width: '58%', borderRadius: 8 }} />
              {c.face_file && <img src={`/api/projects/${projectId}/media/${c.face_file}/raw?b=${bump}`} alt={`${c.name} ${t.faceCard}`} style={{ width: '40%', borderRadius: 8, alignSelf: 'flex-start' }} />}
            </div>
            <figcaption className="section-note"><strong>{c.name}</strong></figcaption>
            {!sample && (
              <div className="actions">
                <input type="text" placeholder={t.cardNote} value={note[c.card] ?? ''} disabled={!!busy || running}
                  onChange={(e) => setNote({ ...note, [c.card]: e.target.value })} style={{ width: '100%' }} />
                <button className="btn small ghost" disabled={!!busy || running}
                  onClick={() => void act(c.card, () => send(`${base}/cards/${c.card}/regenerate`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ note: note[c.card] ?? '' }) }))}>
                  {busy === c.card ? '…' : t.regen}</button>
                <label className="btn small ghost">
                  {t.upload}
                  <input type="file" accept="image/*" hidden disabled={!!busy || running} onChange={(e) => {
                    const f = e.target.files?.[0]
                    if (!f) return
                    const fd = new FormData()
                    fd.append('file', f)
                    void act(c.card, () => send(`${base}/cards/${c.card}/upload`, { method: 'POST', body: fd }))
                  }} />
                </label>
              </div>
            )}
          </figure>
        ))}
      </div>
      {groups.map((g) => {
        const [scene, loc] = g.split('|')
        const here = plates.filter((p) => p.scene === scene && p.location === loc)
          .sort((a, b) => ORDER.indexOf(a.view) - ORDER.indexOf(b.view))
        return (
          <div key={g}>
            <h3><AiText text={here[0]?.place ?? loc} lang={lang} projectId={projectId} /></h3>
            <div className="plate-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 12 }}>
              {here.map((p) => (
                <figure key={p.name} style={{ margin: 0 }}>
                  {p.file ? (
                    <img src={`/api/projects/${projectId}/media/${p.file}/raw?b=${bump}`} alt={t.view[p.view] ?? p.view}
                      style={{ width: '100%', borderRadius: 8 }} />
                  ) : (
                    <div className="section-note" style={{ aspectRatio: '16 / 9', borderRadius: 8, border: '1px dashed var(--sumi-faint, #aaa)',
                      display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{t.empty}</div>
                  )}
                  <figcaption className="section-note">
                    <strong>{t.view[p.view] ?? p.view}</strong>{p.own && <span className="tag" style={{ marginLeft: 6 }}>{t.ownTag}</span>}
                    {p.faces_en && <> — <AiText text={p.faces_en} lang={lang} projectId={projectId} /></>}
                  </figcaption>
                  {!sample && (
                    <div className="actions">
                      {p.file && !p.own && <>
                        <input type="text" placeholder={t.note} value={note[p.name] ?? ''} disabled={!!busy || running}
                          onChange={(e) => setNote({ ...note, [p.name]: e.target.value })} style={{ width: '100%' }} />
                        <button className="btn small ghost" disabled={!!busy || running}
                          onClick={() => void act(p.name, () => send(`${base}/plates/${p.name}/regenerate`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ note: note[p.name] ?? '' }) }))}>
                          {busy === p.name ? '…' : t.regen}</button>
                      </>}
                      <label className="btn small ghost">
                        {t.upload}
                        <input type="file" accept="image/*" hidden disabled={!!busy || running} onChange={(e) => {
                          const f = e.target.files?.[0]
                          if (!f) return
                          const fd = new FormData()
                          fd.append('file', f)
                          void act(p.name, () => send(`${base}/plates/${p.name}/upload`, { method: 'POST', body: fd }))
                        }} />
                      </label>
                    </div>
                  )}
                </figure>
              ))}
            </div>
          </div>
        )
      })}
      <div className="actions">
        {sample ? <span className="section-note">{t.sample}</span> : !running && (
          <button className="btn primary" onClick={() => void act('start', () => send(base, { method: 'POST' }))}>
            {st.status ? t.restart : t.start}</button>
        )}
        {!sample && !running && (
          // 自備人物卡、沒有空景的人：只做人物卡，沒上傳空景的地點用文字描述（後端 plates=false；生成時會警告很隨機）
          <button className="btn ghost" onClick={() => void act('start', () => send(`${base}?plates=false`, { method: 'POST' }))}>
            {t.cardsOnly}</button>
        )}
      </div>
    </section>
  )
}
