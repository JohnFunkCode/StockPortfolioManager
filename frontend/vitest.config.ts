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
        // Measured 2026-07-27 after the arbitrage visualization suite:
        // lines 89.2, statements 87.4, funcs 86.8, branches 70.5.
        // (Previous 2026-07-26: 88.4 / 86.6 / 85.7 / 69.4.)
        // Floors pinned ~1pt under; ratchet only up.
        lines: 88,
        statements: 86,
        functions: 85,
        branches: 69,
      },
    },
  },
});
