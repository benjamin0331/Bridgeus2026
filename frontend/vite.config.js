import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')

  return {
    plugins: [react()],
    server: {
      proxy: {
        '/api': {
          target: env.VITE_PROXY_TARGET || 'http://127.0.0.1:8005',
          changeOrigin: true,
        },
        // 研究者上傳的知識庫影片檔。url 存的是根相對路徑（/media/...），
        // <video src> 會拿 dev server 的 origin（5173）去解析，沒有這條就
        // 404。正式環境由 nginx / Cloudflare Tunnel 對應轉發。
        '/media': {
          target: env.VITE_PROXY_TARGET || 'http://127.0.0.1:8005',
          changeOrigin: true,
        },
        '/ws': {
          target: env.VITE_PROXY_TARGET || 'http://127.0.0.1:8005',
          changeOrigin: true,
          ws: true,
        },
        // Godot 多人連線：轉到本機 headless Godot server（port 8085）。
        // 正式環境由 Cloudflare Tunnel 依路徑轉到常駐 server，這裡是本機對應版；
        // 沒有這條，按 Join 必然連不上。要另外自己起：
        //   GODOT_SERVICE_TOKEN=<同 backend/.env> godot --headless --path godot --server
        //
        // 刻意不 rewrite 掉 /godot-ws 前綴：Cloudflare Tunnel 的 ingress 不會
        // 剝前綴，本機保持一致才不會出現「本機通、正式站掛」。Godot 的
        // WebSocketMultiplayerPeer server 不看路徑，兩種寫法它都收，所以對齊
        // 正式環境是這裡唯一的判準。
        //
        // 註：Godot 的靜態檔不需要 proxy——export_presets.cfg 已把 web build
        // 直接匯出到 frontend/public/godot/，由 Vite 當一般靜態資源服務
        // （thread_support=false，不需要 COOP/COEP 標頭）。
        '/godot-ws': {
          target: env.VITE_GODOT_WS_TARGET || 'ws://127.0.0.1:8085',
          changeOrigin: true,
          ws: true,
        },
      },
    },
  }
})
