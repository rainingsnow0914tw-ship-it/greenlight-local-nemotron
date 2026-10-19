/**
 * 背景工作面板的輪詢節流：跑的時候每 3 秒問一次，沒在跑就 30 秒問一次。
 *
 * 第三世試跑：段落頁四個面板（導演、台詞、參考卡、成片）不管有沒有在跑都每 3 秒問一次，
 * 台詞那支每次回整份 133 句 —— 20 分鐘累積五千多個請求。按「開始」的地方都會立刻自己問一次，
 * 所以閒著時放慢不會讓按鈕看起來沒反應。
 */
export function makePollGate(every = 10): (status?: string | null) => boolean {
  let tick = 0
  return (status) => status === 'running' || ++tick % every === 0
}
