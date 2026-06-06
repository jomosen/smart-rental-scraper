import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
  ],
  build: {
    // Contract: app.py reads from web/dist — keep this path in sync.
    outDir: 'dist',
    target: 'esnext',
  },
  server: {
    proxy: {
      // Forward /api/* to the FastAPI backend in dev; build talks directly.
      '/api': 'http://localhost:8000',
    },
  },
})
