import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The app calls the backend same-origin at /api/…; both the dev server and the
// preview server proxy that prefix to the real API. This keeps the browser
// free of CORS entirely, so the app works on any port or host (including
// other devices on the LAN) without touching the backend's origin allow-list.
const apiProxy = {
  '/api': {
    target: 'http://localhost:8000',
    changeOrigin: true,
    rewrite: (path: string) => path.replace(/^\/api/, ''),
  },
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: apiProxy,
  },
  preview: {
    proxy: apiProxy,
  },
})
