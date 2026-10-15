import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // 開發時把 /api 轉給 FastAPI，前端就不用管 CORS 也不用寫死主機。
    // 正式部署時前後端同源，這段用不到。
    proxy: {
      // 後端 port 不用預設的 8000：Windows 上 uvicorn 被強制結束後，socket 可能
      // 留在系統裡不釋放（process 表查不到 PID，`Get-NetTCPConnection` 卻仍顯示持有 port），
      // 新的 uvicorn 會照樣印出 "Uvicorn running on ..." 但收不到任何連線，
      // 結果是 code 改了完全沒生效且毫無錯誤訊息。遇到時換 port 最快。
      // 這裡改了，後端啟動指令也要跟著改。
      '/api': {
        target: 'http://127.0.0.1:8020',
        changeOrigin: true,
      },
    },
  },
})
