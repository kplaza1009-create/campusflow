import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development the dev server proxies /api to FastAPI, so the browser sees one
// origin: cookies stay first-party and no CORS is involved.
const apiTarget = process.env.CAMPUSFLOW_API_URL ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(process.env.PORT ?? 5173),
    strictPort: true,
    proxy: { '/api': { target: apiTarget, changeOrigin: false } },
  },
  preview: {
    port: Number(process.env.PORT ?? 4173),
    strictPort: true,
    proxy: { '/api': { target: apiTarget, changeOrigin: false } },
  },
})
