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
        // Measured 2026-08-11 at the end of issue #147 (Part G, 608 tests):
        // lines 90.75, statements 88.81, funcs 88.65, branches 73.39.
        // (Previous 2026-07-27: 89.6 / 87.6 / 87.5 / 70.8.)
        //
        // The floors were deliberately left alone through #147's intermediate
        // PRs — the Watchlist and Fundamentals pages arrived with their tests
        // but the numbers moved every PR, and ratcheting each time would have
        // made an unrelated red build the way you learn a page shipped. This
        // is the single ratchet the plan reserved for the end.
        lines: 90,
        statements: 88,
        functions: 88,
        branches: 72,
      },
    },
  },
});
