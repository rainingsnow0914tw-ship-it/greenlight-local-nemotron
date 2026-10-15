/**
 * 學語言模式（專案層）：原聲講什麼語言、字幕下行給誰看。
 *
 * 為什麼放在定調旁邊：這跟風格一樣是「整片一致」的決定，而且要在導台詞之前定 ——
 * 台詞導演照這兩格把劇本台詞改寫成原聲語言的口語、寫各語字幕。
 * 配方來自作者自己的《第三世》日文動畫：日文原聲＋漢字標假名＋中／英字幕，幫學到一半卡住的人。
 */
import { useEffect, useState } from 'react'
import type { Lang } from '../api'

type Langs = { study_mode: boolean; name_glossary: string; film_language: string; learner_languages: string[]; film_options: string[]; learner_options: string[] }

const NAME: Record<Lang, Record<string, string>> = {
  zh: { ja: '日本語', zh: '中文', en: 'English', ko: '한국어' },
  en: { ja: 'Japanese', zh: 'Chinese', en: 'English', ko: 'Korean' },
  ja: { ja: '日本語', zh: '中国語', en: '英語', ko: '韓国語' },
}

const T = {
  zh: {
    title: '學語言模式', on: '開啟', off: '關閉',
    lead: '用你自己的故事學語言：角色講外語原聲，字幕上行是原聲（漢字上面標讀音），下行是你的母語。',
    why: '台詞不是機翻：Nemotron 台詞導演看誰在跟誰講話、熟不熟、地位高低，寫出這個角色此刻真的會講的話（越熟越口語），再寫出帶著同樣語氣的字幕和「為什麼這樣說」。',
    film: '原聲語言（角色講什麼）', learners: '字幕下行（母語，可多選，一種燒一版）',
    first: '第一個也是學習筆記的解說語言。', saved: '已儲存',
    names: '名字表（一行一個：名字=原聲怎麼唸/英文字幕怎麼寫）', namesHelp: '名字要跟你作品既有的譯名一致；只被提到、不出現在畫面上的角色（AI、寵物）程式抓不到，要寫在這裡，不然名字會被當成普通的詞翻掉。英文可省略。',
    namesSave: '儲存名字表',
  },
  en: {
    title: 'Study mode', on: 'On', off: 'Off',
    lead: 'Learn a language from your own story: characters speak the target language; the subtitle shows the spoken line (with readings above the kanji) and your own language below.',
    why: 'Not machine translation: the Nemotron line director looks at who is talking to whom, how close they are and who outranks whom, writes what this character would really say right now (closer means more casual), then a subtitle with the same tone and a note on why it is said that way.',
    film: 'Spoken language (what the characters speak)', learners: 'Subtitle language (yours — pick one or more, one version each)',
    first: 'The first one is also the language of the study notes.', saved: 'Saved',
    names: 'Names (one per line: name=how it is said/how English subtitles spell it)', namesHelp: 'Keep names consistent with your existing translations. Characters who are only mentioned and never on screen (an AI, a pet) cannot be detected — list them here, or their names get translated as ordinary words. The English part is optional.',
    namesSave: 'Save names',
  },
  ja: {
    title: '語学モード', on: 'オン', off: 'オフ',
    lead: '自分の物語で語学を：キャラクターは学びたい言語で話し、字幕の上段はセリフ（漢字にふりがな）、下段はあなたの言語。',
    why: '機械翻訳ではありません。Nemotron のセリフ演出が、誰が誰に話しているか、どれくらい親しいか、立場の上下を見て、このキャラクターが今本当に言う言葉を書きます（親しいほど口語に）。同じ口調の字幕と「なぜこう言うのか」も付けます。',
    film: '話す言語（キャラクターのセリフ）', learners: '字幕の下段（あなたの言語・複数可、言語ごとに一本）',
    first: '最初の言語が学習ノートの解説言語になります。', saved: '保存しました',
    names: '名前リスト（1行に1つ：名前=読み方/英語字幕の表記）', namesHelp: '既存の訳名とそろえてください。話に出てくるだけで画面に出ない人物（AI・ペット）は検出できません。ここに書かないと、名前が普通の言葉として訳されてしまいます。英語は省略可。',
    namesSave: '名前リストを保存',
  },
}

export function LanguagePanel({ lang, projectId, onChanged }: { lang: Lang; projectId: string; onChanged?: () => void }) {
  const t = T[lang]
  const [st, setSt] = useState<Langs | null>(null)
  const [msg, setMsg] = useState('')
  const [names, setNames] = useState<string | null>(null)   // 還沒存的名字表（null＝沒改過）

  useEffect(() => {
    void fetch(`/api/projects/${projectId}/languages`).then(async (r) => r.ok && setSt(await r.json()))
  }, [projectId])

  const save = async (patch: Partial<Langs>) => {
    const r = await fetch(`/api/projects/${projectId}/languages`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch),
    })
    if (r.ok) {
      setSt(await r.json())
      setMsg(t.saved)
      setTimeout(() => setMsg(''), 1500)
      onChanged?.()
    }
  }

  if (!st) return null
  const toggleLearner = (code: string) => {
    const has = st.learner_languages.includes(code)
    const next = has ? st.learner_languages.filter((x) => x !== code) : [...st.learner_languages, code]
    if (next.length) void save({ learner_languages: next })
  }

  return (
    <section className="section">
      <div className="section-head">
        <h2>{t.title}</h2>
        <div className="actions" style={{ margin: 0 }}>
          <button className={`btn chip ${st.study_mode ? 'on' : ''}`} onClick={() => void save({ study_mode: true })}>{t.on}</button>
          <button className={`btn chip ${!st.study_mode ? 'on' : ''}`} onClick={() => void save({ study_mode: false })}>{t.off}</button>
        </div>
        {msg && <span className="section-note">{msg}</span>}
      </div>
      <p className="lang-lead">{t.lead}</p>
      {st.study_mode && (
        <>
          <div className="notice">{t.why}</div>
          <div className="lang-row">
            <div className="stat-label">{t.film}</div>
            <div className="chips">
              {st.film_options.map((c) => (
                <button key={c} className={`btn chip ${st.film_language === c ? 'on' : ''}`}
                  onClick={() => void save({ film_language: c })}>{NAME[lang][c] ?? c}</button>
              ))}
            </div>
          </div>
          <div className="lang-row">
            <div className="stat-label">{t.learners}</div>
            <div className="chips">
              {st.learner_options.filter((c) => c !== st.film_language).map((c) => (
                <button key={c} className={`btn chip ${st.learner_languages.includes(c) ? 'on' : ''}`}
                  onClick={() => toggleLearner(c)}>
                  {st.learner_languages.includes(c) ? `${st.learner_languages.indexOf(c) + 1}. ` : ''}{NAME[lang][c] ?? c}
                </button>
              ))}
            </div>
            <div className="section-note">{t.first}</div>
          </div>
          <div className="lang-row">
            <div className="stat-label">{t.names}</div>
            <textarea className="lang-names" rows={3} value={names ?? st.name_glossary}
              placeholder={'深息=シェンシー/Shenxi\n月音=つきね/Tsukine\nLisa=リサ'} onChange={(e) => setNames(e.target.value)} />
            <div className="section-note">{t.namesHelp}</div>
            {names !== null && names !== st.name_glossary && (
              <div><button className="btn primary small" onClick={() => void save({ name_glossary: names }).then(() => setNames(null))}>{t.namesSave}</button></div>
            )}
          </div>
        </>
      )}
    </section>
  )
}

export const LANG_NAME = NAME
