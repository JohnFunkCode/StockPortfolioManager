/**
 * E2E config for the chat sidekick. Boots the real FastAPI tier with
 * CHAT_FAKE=1 (deterministic scripted LLM, keyless) against the TEST database
 * plus the Vite dev server. Ports 5001/5173 must be free — we deliberately do
 * NOT reuse an existing API server, so a dev instance pointed at prod can
 * never be picked up by tests.
 */
import { defineConfig } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');

function testDbDsn(): string {
  // Mirror the backend test preamble: QUANTCORE_TEST_DB_DSN from ../.env wins.
  try {
    const env = readFileSync(resolve(repoRoot, '.env'), 'utf-8');
    for (const line of env.split('\n')) {
      if (line.trim().startsWith('QUANTCORE_TEST_DB_DSN=')) {
        return line.split('=').slice(1).join('=').trim();
      }
    }
  } catch {
    /* no .env (CI) — fall through */
  }
  return process.env.QUANTCORE_TEST_DB_DSN ?? process.env.QUANTCORE_DB_DSN ?? '';
}

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  retries: 0,
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: `${repoRoot}/.venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 5001`,
      cwd: repoRoot,
      url: 'http://127.0.0.1:5001/api/health',
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        CHAT_FAKE: '1',
        QUANTCORE_DB_DSN: testDbDsn(),
      },
    },
    {
      command: 'npm run dev',
      url: 'http://localhost:5173',
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
