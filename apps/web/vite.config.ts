import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig(({ mode }) => ({
  plugins: [
    react(),
    ...(mode === 'test'
      ? [
          {
            name: 'explicit-preparation-fixture-routes',
            configureServer(server: import('vite').ViteDevServer) {
              server.middlewares.use((request, _response, next) => {
                const path = new URL(request.url ?? '/', 'http://fixture.local').pathname
                if (path === '/' || path === '/settings' || path.startsWith('/w/'))
                  request.url = '/test.html'
                next()
              })
            },
          },
        ]
      : []),
  ],
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
  test: { environment: 'jsdom', setupFiles: ['./src/test-setup.ts'] },
}))
