import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:3001',
        changeOrigin: true,
      },
      // Proxy WebSocket vehicle stream to the backend server
      '/vehicles': {
        target: 'ws://localhost:3001',
        ws: true,
        changeOrigin: true,
      }
    }
  }
})
