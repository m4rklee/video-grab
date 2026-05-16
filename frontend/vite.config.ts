import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    // 避开 5173/5174 等常见端口，减少与其它本地前端（如评测平台）串台
    port: 9280,
    strictPort: true,
    proxy: {
      '/api': {
        // 8000 常被其它项目占用；本仓库默认连 VideoGrab 后端 8028（可用 VITE_API_TARGET 覆盖）
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
