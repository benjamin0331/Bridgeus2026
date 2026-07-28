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
        // Godot 多人連線：轉到本機 headless Godot server（port 8085）。
        // 正式環境由 Cloudflare Tunnel 轉到常駐 server，這裡是本機對應版。
        '/godot-ws': {
          target: env.VITE_GODOT_WS_TARGET || 'ws://127.0.0.1:8085',
          changeOrigin: true,
          ws: true,
          rewrite: (path) => path.replace(/^\/godot-ws/, ''),
        },
      },
    },
  }
})
