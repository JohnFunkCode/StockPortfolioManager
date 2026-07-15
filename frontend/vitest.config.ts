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
        // Baseline 2026-07-14: lines 23.5, statements 22.4, funcs 16.5, branches 16.2.
        lines: 22,
        statements: 21,
        functions: 15,
        branches: 15,
      },
    },
  },
});
