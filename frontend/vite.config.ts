import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    // 避开 5173/5174 等常见端口，减少与其它本地前端（如评测平台）串台
    port: 9280,
    strictPort: true,
    proxy: {
      // 搜索流式 NDJSON：单独代理并拉长超时，减轻 dev 代理缓冲（仍异常时在设置里直连后端）
      '/api/search': {
        target: process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8028',
        changeOrigin: true,
        timeout: 0,
        proxyTimeout: 0,
      },
      '/api': {
        target: process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8028',
        changeOrigin: true,
      },
    },
  },
  preview: {
    port: 9280,
    strictPort: true,
  },
})
