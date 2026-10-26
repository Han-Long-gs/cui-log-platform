// ref:https://vite.dev/config/server-options#server-proxy
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8001',  // query-api dev host
    },
  },
})
