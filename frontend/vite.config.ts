import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// The dev server proxies /api/* to the API tier server-side, so the browser stays
// same-origin (no CORS). Target defaults to the local API; point it at the prod
// Cloud Run URL by setting VITE_API_PROXY_TARGET (e.g. in frontend/.env.local).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const target = env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:5001';
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target,
          changeOrigin: true,
        },
      },
    },
  };
});
