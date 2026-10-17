import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发代理：/chat 与 /api 转发到本地 FastAPI 后端（电网智能体）
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 3001,
    proxy: {
      '/chat': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
