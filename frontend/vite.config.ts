import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    sourcemap: false,
    assetsInlineLimit: 0, // die CSP (font-src 'self') erlaubt keine data:-Schriften
  },
  server: {
    // nur Dev: der Manager läuft per HTTPS mit selbst signiertem Zertifikat
    proxy: { '/api': { target: 'https://localhost:8443', secure: false } },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
