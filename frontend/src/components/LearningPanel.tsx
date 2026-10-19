/**
 * 學習迴路面板 —— 把 ClickHouse 在後台做的事攤到看得見的地方。
 *
 * 為什麼存在：
 *    迴路一直在轉（裁決寫入 → 段落完成提煉 → 規劃時帶著記憶），
 *    但全在後台，使用者在介面上感受不到 ClickHouse 有什麼作用。
 *    使用者看不到的迴路等於不存在。
 *
 * 為什麼首頁也用這一個：
 *    首頁若另做一個簡化版組件，代價不是簡陋，是兩份 UI 必然不同步 ——
 *    例如一份把 `distinct_shots` 讀錯 key，「涵蓋鏡頭」靜默顯示 0，
 *    另一份同一格卻是對的，因為它是另一份 code。
 *
 *    所以不是「把首頁那份補齊功能」，是只留一份。
 *    一個組件、兩種資料範圍（`projectId` 給了就是單專案，沒給就是跨專案）。
 *
 * 首頁不顯示「資料庫連不上」的降級提示（`hideWhenUnavailable`）：
 *    專案內頁要講，因為使用者需要知道為什麼沒累積新經驗；
 *    首頁講了只是在陌生人面前掛一塊故障告示。功能不受影響（有降級模式）。
 */

import { Icon } from './Icons'
import { useEffect, useState } from 'react'
import { api, t, type Lang, type LearningState, type Localized } from '../api'

const COPY: Record<string, Localized> = {
  rowsShow: { zh: '看原始紀錄', en: 'View raw records', ja: '生データを見る' },
  rowsHide: { zh: '收起原始紀錄', en: 'Hide raw rows', ja: '生データを閉じる' },
  rowsLoading: { zh: '查詢中…', en: 'Querying…', ja: 'クエリ中…' },
  rowsMeta: {
    zh: '最近 {n} 筆，直接查自 {table}（{host}）。每一次生成與裁決都會寫一筆。',
    en: 'Latest {n} rows, queried live from {table} ({host}). Every generation and verdict writes one row.',
    ja: '最新 {n} 件、{table}（{host}）から直接取得。生成と判断のたびに 1 行記録されます。',
  },
  rowsEmpty: { zh: '這個專案還沒有紀錄 —— 生成並裁決過一鏡之後就會出現。', en: 'No rows for this project yet — they appear once you generate and judge a shot.', ja: 'まだ記録がありません。カットを生成して判断すると表示されます。' },
  colTime: { zh: '時間', en: 'Time', ja: '時刻' },
  colShot: { zh: '鏡頭', en: 'Shot', ja: 'カット' },
  colOutcome: { zh: '結果', en: 'Outcome', ja: '結果' },
  colPredicted: { zh: '當時預測', en: 'Predicted', ja: '予測' },
  colReason: { zh: '打回原因', en: 'Reason', ja: '却下理由' },
  colCost: { zh: '花費', en: 'Cost', ja: '費用' },
  colLatency: { zh: '耗時', en: 'Latency', ja: '所要' },
  colModel: { zh: '模型', en: 'Model', ja: 'モデル' },
  title: { zh: '學習迴路', en: 'Learning loop', ja: '学習ループ' },
  why: {
    zh: '每一次你按「通過」或「重拍」，判決都會寫進你電腦裡的學習紀錄。系統從這些紀錄學會「什麼樣的鏡頭怎麼拍才會過」，下次規劃時帶著這些經驗。',
    en: 'Every approve / reshoot verdict is written to the learning records on your computer. The system learns what kinds of shots pass, and plans the next batch with that experience.',
    ja: '「承認」「撮り直し」の判断はすべてあなたのPCの学習記録に保存されます。システムはそこから「通るカットの作り方」を学び、次の計画に活かします。',
  },
  /* 首頁版：講的是「你自己累積下來的」，不是「別人的經驗」。
     不能寫成「你的第一支片就吃得到前面所有人的經驗」—— 那是隱私問題：
     它暗示不同使用者的資料互相餵食。實際上這是你自己歷次拍片的實測結果，
     用得越多越準。措辭要跟事實一致，也要跟未來的多租戶隔離一致。 */
  whyGlobal: {
    zh: '每一次生成與裁決都寫進你電腦裡的學習紀錄。這裡是你自己歷次拍片累積下來的實測結果 —— 用得越多，系統越清楚「什麼樣的鏡頭怎麼拍才會過」，你也越省錢。',
    en: 'Every generation and verdict is written to the learning records on your computer. These are observations from your own runs — the more you shoot, the better it knows what works, and the less you spend getting there.',
    ja: '生成と判断のすべてをあなたのPCの学習記録に保存します。これはあなた自身のこれまでの実測の蓄積 —— 使うほど「通るカットの作り方」が分かり、費用も下がっていきます。',
  },
  titleGlobal: { zh: '學習迴路（累積至今）', en: 'Learning loop (accumulated)', ja: '学習ループ（これまでの蓄積）' },
  records: { zh: '筆生成紀錄', en: 'generation records', ja: '件の生成記録' },
  firstPass: { zh: '一次過關率', en: 'First-pass rate', ja: '一発OK率' },
  rejected: { zh: '被打回重拍', en: 'Reshoots', ja: '撮り直し' },
  sampleFinal: {
    zh: '這支範例：{n} 鏡全部收下（作者做好的最終版）。',
    en: 'This sample: all {n} shots accepted (the author’s finished cut).',
    ja: 'このサンプル：{n} カットすべて採用（作者が仕上げた最終版）。',
  },
  shots: { zh: '涵蓋鏡頭', en: 'Distinct shots', ja: 'カット数' },
  notesTitle: { zh: '系統提煉出的規律', en: 'What the system has learned', ja: 'システムが学んだこと' },
  notesMeta: { zh: '基於 {n} 筆實測 · {date}', en: 'From {n} observations · {date}', ja: '{n} 件の実測より · {date}' },
  collecting: {
    zh: '紀錄還不足 {n} 筆，系統正在收集經驗 —— 每生成一鏡就多學一筆。',
    en: 'Fewer than {n} records so far — the system learns from every shot you generate.',
    ja: '記録が {n} 件未満です。生成のたびに学習データが増えます。',
  },
  degraded: {
    zh: '資料庫暫時連不上，系統改用內建先驗值運作（功能不受影響，只是不會累積新經驗）。',
    en: 'Database temporarily unreachable — running on built-in priors (nothing breaks, but no new learning).',
    ja: 'データベースに接続できません。内蔵の事前値で動作中です（機能に影響なし）。',
  },
}

function fill(s: string, vars: Record<string, string | number>): string {
  return s.replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? ''))
}

export function LearningPanel({
  lang,
  projectId,
  hideWhenUnavailable = false,
}: {
  lang: Lang
  /** 給了 = 這個專案的紀錄；省略 = 跨專案全站累積（首頁）。 */
  projectId?: string
  /** 連不上資料庫時整區消失，不顯示降級提示。首頁用 —— 見檔頭。 */
  hideWhenUnavailable?: boolean
}) {
  const [data, setData] = useState<LearningState | null>(null)
  /** 讀失敗 = 整區不顯示。要跟「還在載入」分開，否則骨架會永遠留在畫面上。 */
  const [failed, setFailed] = useState(false)
  /** 原始紀錄。null = 還沒點開（刻意懶載入，不要一進頁面就打 ClickHouse） */
  const [records, setRecords] = useState<Array<Record<string, string | number | boolean>> | null>(null)
  const [meta, setMeta] = useState<{ table?: string; endpoint?: string }>({})
  const [loadingRows, setLoadingRows] = useState(false)

  useEffect(() => {
    let alive = true
    setData(null)
    setFailed(false)
    setRecords(null)
    api
      .learning(projectId)
      .then((d) => {
        if (alive) setData(d)
      })
      .catch(() => {
        /* 面板是加分資訊，讀不到就不顯示，不打擾主流程 */
        if (alive) setFailed(true)
      })
    return () => {
      alive = false
    }
  }, [projectId])

  async function openRecords() {
    if (records) {
      setRecords(null)
      return
    }
    setLoadingRows(true)
    try {
      const r = await api.learningRecords(projectId, 40)
      setRecords(r.rows || [])
      setMeta({ table: r.table, endpoint: r.endpoint })
    } catch {
      setRecords([])
    } finally {
      setLoadingRows(false)
    }
  }

  const isGlobal = !projectId
  const title = t(isGlobal ? COPY.titleGlobal : COPY.title, lang)

  if (failed) return null

  /* 載入中撐住版面。
     若載入前回 null、拿到資料才整區冒出來，那個「突然蹦出來」會被讀成「很慢」，
     其實 API 只花 0.35 秒。慢的體感來自版面跳動，不是延遲。
     骨架照真實內容的結構擺（標題／說明／三格統計／按鈕），高度對齊就不會跳。 */
  if (!data) {
    return (
      <section className="section" aria-busy="true">
        <div className="section-head">
          <h2>
            <Icon name="learning" size={80} className="section-icon" />
            {title}
          </h2>
        </div>
        <p className="prompts-note">{t(isGlobal ? COPY.whyGlobal : COPY.why, lang)}</p>
        <div className="stats">
          {[0, 1, 2].map((i) => (
            <div key={i}>
              <div className="skeleton skeleton-label" />
              <div className="skeleton skeleton-value" />
            </div>
          ))}
        </div>
        <div style={{ margin: '18px 0' }}>
          <div className="skeleton skeleton-btn" />
        </div>
      </section>
    )
  }

  /* 首頁：資料庫連不上就整區消失，不在陌生人面前掛故障告示 —— 見檔頭。 */
  if (hideWhenUnavailable && (!data.connected || !data.records)) return null

  const date = (data.refined_notes?.refined_at || '').slice(0, 10)

  return (
    <section className="section">
      <div className="section-head">
        <h2>
          <Icon name="learning" size={80} className="section-icon" />
          {title}
        </h2>
        <span className="section-note">
          {data.database} · {data.records} {t(COPY.records, lang)}
        </span>
      </div>
      <p className="prompts-note">{t(isGlobal ? COPY.whyGlobal : COPY.why, lang)}</p>

      {!data.connected && <div className="notice warn">{t(COPY.degraded, lang)}</div>}

      {data.sample_final && (
        <div className="notice">{fill(t(COPY.sampleFinal, lang), { n: data.sample_final.accepted })}</div>
      )}

      {data.connected && !data.sample_final && (
        <div className="stats">
          <div>
            <div className="stat-label">{t(COPY.firstPass, lang)}</div>
            <div className="stat-value">
              {data.first_pass_rate != null
                ? `${Math.round(data.first_pass_rate * 100)}%`
                : '—'}
            </div>
          </div>
          <div>
            <div className="stat-label">{t(COPY.rejected, lang)}</div>
            <div className="stat-value">{data.rejected}</div>
          </div>
          <div>
            <div className="stat-label">{t(COPY.shots, lang)}</div>
            <div className="stat-value">{data.distinct_shots}</div>
          </div>
        </div>
      )}

      {/* 舉證用：
          上面那些統計是系統的宣稱，這裡是原始資料 ——
          帶時間戳、鏡號、模型 id、實際花費與延遲的原始列編不出來。 */}
      {data.connected && !data.sample_final && (
        <div style={{ margin: '18px 0' }}>
          <button className="btn" onClick={() => void openRecords()} disabled={loadingRows}>
            {loadingRows
              ? t(COPY.rowsLoading, lang)
              : records
                ? t(COPY.rowsHide, lang)
                : t(COPY.rowsShow, lang)}
          </button>
          {records && (
            <>
              <p className="prompts-lang-note" style={{ marginTop: 10 }}>
                {fill(t(COPY.rowsMeta, lang), {
                  n: records.length,
                  table: meta.table || 'generation_ledger',
                  host: meta.endpoint || 'this computer',
                })}
              </p>
              <div style={{ overflowX: 'auto' }}>
                <table className="ledger-table">
                  <thead>
                    <tr>
                      <th>{t(COPY.colTime, lang)}</th>
                      <th>{t(COPY.colShot, lang)}</th>
                      <th>#</th>
                      <th>{t(COPY.colOutcome, lang)}</th>
                      <th>{t(COPY.colPredicted, lang)}</th>
                      <th>{t(COPY.colReason, lang)}</th>
                      <th>{t(COPY.colCost, lang)}</th>
                      <th>{t(COPY.colLatency, lang)}</th>
                      <th>{t(COPY.colModel, lang)}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {records.map((r, i) => (
                      <tr key={i}>
                        <td>{String(r.created_at).slice(0, 19)}</td>
                        <td>{String(r.shot_id)}</td>
                        <td>{String(r.attempt)}</td>
                        <td>{String(r.outcome)}</td>
                        <td>{Math.round(Number(r.predicted_success_rate) * 100)}%</td>
                        <td title={String(r.rejection_reason || '')}>
                          {String(r.rejection_reason || '').slice(0, 40) || '—'}
                        </td>
                        <td>${Number(r.cost_usd).toFixed(3)}</td>
                        <td>{String(r.latency_seconds)}s</td>
                        <td>{String(r.model_id || '—')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {records.length === 0 && (
                <p className="prompts-lang-note">{t(COPY.rowsEmpty, lang)}</p>
              )}
            </>
          )}
        </div>
      )}

      {data.refined_notes ? (
        <div className="notice">
          <strong>{t(COPY.notesTitle, lang)}</strong>
          <div style={{ marginTop: 8, whiteSpace: 'pre-wrap' }}>
            {data.refined_notes.text}
          </div>
          <div className="prompts-lang-note" style={{ marginTop: 8 }}>
            {fill(t(COPY.notesMeta, lang), {
              n: data.refined_notes.sample_size,
              date,
            })}
          </div>
        </div>
      ) : (
        data.connected &&
        data.records < data.refine_threshold && (
          <p className="prompts-lang-note">
            {fill(t(COPY.collecting, lang), { n: data.refine_threshold })}
          </p>
        )
      )}
    </section>
  )
}
