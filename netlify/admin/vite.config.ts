import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const dir = fileURLToPath(new URL('.', import.meta.url))

export default defineConfig({
  base: './',
  plugins: [react()],
  server: {
    proxy: { '/api': 'https://smartylc.netlify.app' },
  },
  build: {
    outDir: '../public',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        index: `${dir}index.html`,
        admin: `${dir}admin.html`,
      },
    },
  },
})
