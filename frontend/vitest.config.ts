import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    // jsdom disables localStorage on the default about:blank origin.
    environmentOptions: { jsdom: { url: 'http://localhost:5173' } },
    globals: true,
    setupFiles: ['./src/setupTests.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      // Count every source file, tested or not — the honest denominator.
      all: true,
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/**/*.test.{ts,tsx}',
        'src/setupTests.ts',
        'src/main.tsx',
        'src/vite-env.d.ts',
      ],
      reporter: ['text-summary', 'html', 'json-summary'],
      // Ratchet floors: pinned ~1pt under the measured baseline; only ever
      // raised (see deploy.yml frontend-gate). Not aspirations — regressions.
      thresholds: {
        // Measured 2026-07-24 after the 85%-campaign: lines 85.4, statements 83.4,
        // funcs 78.7, branches 65.8. Floors pinned ~1pt under; ratchet only up.
        lines: 84,
        statements: 82,
        functions: 77,
        branches: 64,
      },
    },
  },
});
