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
        '/ws': {
          target: env.VITE_PROXY_TARGET || 'http://127.0.0.1:8005',
          changeOrigin: true,
          ws: true,
        },
        // 本機開發用的路徑分流，對齊正式環境 Cloudflare Tunnel 的 ingress 規則
        // （部署說明的「對外路徑」表）：/godot/* 是 Godot Web 匯出的靜態檔，
        // /godot-ws 是 headless Godot server 的多人連線。正式環境由 Tunnel 依
        // 路徑分流，本機沒有對應物，所以要在這裡補——沒有這兩條，從 /chat 開
        // Godot iframe 必然 404、按 Join 必然連不上。
        //
        // 兩者都要另外自己起（見 godot/CLAUDE.md「Running」）：
        //   靜態檔  cd godot && python3 -m http.server 8090
        //   連線    GODOT_SERVICE_TOKEN=<同 backend/.env> \
        //           godot --headless --path godot --server
        '/godot': {
          target: 'http://127.0.0.1:8090',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/godot/, ''),
        },
        '/godot-ws': {
          target: 'ws://127.0.0.1:8085',
          changeOrigin: true,
          ws: true,
        },
      },
    },
  }
})
