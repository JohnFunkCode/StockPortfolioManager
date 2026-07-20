/**
 * KeyVaultContext lifecycle tests (Phase 4): absent → add → unlocked ⇄
 * locked → remove → absent, the one-passphrase rule, and the 30-minute
 * sliding auto-lock (fake timers limited to setTimeout/clearTimeout so
 * fake-indexeddb's own scheduling stays real).
 *
 * Real crypto with production KDF parameters — the round-trips here are the
 * proof that what the context stores, a fresh session can unlock.
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import type { ReactNode } from 'react';

import {
  AUTO_LOCK_MS,
  KeyVaultProvider,
  useKeyVault,
  useKeyVaultOptional,
} from './KeyVaultContext';
import { WRONG_PASSPHRASE_MESSAGE } from './vaultCrypto';
import { getAllRecords } from './vaultStore';

const API_KEY = 'sk-ant-test-key-abcd';
const PASSPHRASE = 'correct horse battery staple';

const wrapper = ({ children }: { children: ReactNode }) => (
  <KeyVaultProvider>{children}</KeyVaultProvider>
);

/** Mount under real timers (the initial IndexedDB read needs them); tests
 * that exercise the auto-lock switch to fake timers AFTER this returns. */
async function renderVault() {
  const view = renderHook(() => useKeyVault(), { wrapper });
  await waitFor(() => expect(view.result.current.ready).toBe(true));
  return view;
}

async function addAnthropicKey(
  result: { current: ReturnType<typeof useKeyVault> },
  overrides: Partial<Parameters<ReturnType<typeof useKeyVault>['addKey']>[0]> = {},
) {
  await act(async () => {
    await result.current.addKey({
      provider: 'anthropic',
      apiKey: API_KEY,
      passphrase: PASSPHRASE,
      label: 'Personal key',
      ...overrides,
    });
  });
}

describe('KeyVaultContext', () => {
  beforeEach(() => {
    Object.defineProperty(globalThis, 'indexedDB', {
      value: new IDBFactory(),
      configurable: true,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('starts absent with no keys', async () => {
    const { result } = await renderVault();
    expect(result.current.status('anthropic')).toBe('absent');
    expect(result.current.keys).toEqual([]);
    expect(result.current.getPlaintextKey('anthropic')).toBeNull();
  });

  it('addKey stores the wrapped record, exposes metadata, and unlocks', async () => {
    const persist = vi.fn().mockResolvedValue(true);
    Object.defineProperty(navigator, 'storage', {
      value: { persist },
      configurable: true,
    });
    const { result } = await renderVault();
    await addAnthropicKey(result);

    expect(result.current.status('anthropic')).toBe('unlocked');
    expect(result.current.getPlaintextKey('anthropic')).toBe(API_KEY);
    expect(result.current.keys).toEqual([
      expect.objectContaining({
        provider: 'anthropic',
        label: 'Personal key',
        last4: 'abcd',
        status: 'unlocked',
      }),
    ]);
    // Vault creation asks the browser for durable storage.
    expect(persist).toHaveBeenCalledTimes(1);
    // What hit IndexedDB is ciphertext, not the key.
    const [record] = await getAllRecords();
    expect(JSON.stringify(record)).not.toContain(API_KEY);
    expect(JSON.stringify(record)).not.toContain(PASSPHRASE);
  });

  it('a fresh session sees the stored key locked and unlocks it', async () => {
    const first = await renderVault();
    await addAnthropicKey(first.result);
    first.unmount();

    const { result } = await renderVault();
    expect(result.current.status('anthropic')).toBe('locked');
    expect(result.current.getPlaintextKey('anthropic')).toBeNull();

    await act(async () => {
      await result.current.unlock(PASSPHRASE);
    });
    expect(result.current.status('anthropic')).toBe('unlocked');
    expect(result.current.getPlaintextKey('anthropic')).toBe(API_KEY);
  });

  it('unlock with the wrong passphrase fails and stays locked', async () => {
    const first = await renderVault();
    await addAnthropicKey(first.result);
    first.unmount();

    const { result } = await renderVault();
    await expect(
      act(async () => {
        await result.current.unlock('not the passphrase');
      }),
    ).rejects.toThrow(WRONG_PASSPHRASE_MESSAGE);
    expect(result.current.status('anthropic')).toBe('locked');
    expect(result.current.getPlaintextKey('anthropic')).toBeNull();
  });

  it('one passphrase covers the vault: a second provider must match it', async () => {
    const { result } = await renderVault();
    await addAnthropicKey(result);

    await expect(
      act(async () => {
        await result.current.addKey({
          provider: 'openai',
          apiKey: 'sk-openai-test-wxyz',
          passphrase: 'a different passphrase',
          label: 'Other',
        });
      }),
    ).rejects.toThrow(WRONG_PASSPHRASE_MESSAGE);
    expect(result.current.status('openai')).toBe('absent');

    await act(async () => {
      await result.current.addKey({
        provider: 'openai',
        apiKey: 'sk-openai-test-wxyz',
        passphrase: PASSPHRASE,
        label: 'Other',
      });
    });
    expect(result.current.status('openai')).toBe('unlocked');
    // ...and one unlock opens both.
    act(() => result.current.lock());
    await act(async () => {
      await result.current.unlock(PASSPHRASE);
    });
    expect(result.current.getPlaintextKey('anthropic')).toBe(API_KEY);
    expect(result.current.getPlaintextKey('openai')).toBe('sk-openai-test-wxyz');
  });

  it('rotateKey replaces the material under the existing passphrase', async () => {
    const { result } = await renderVault();
    await addAnthropicKey(result);

    await expect(
      act(async () => {
        await result.current.rotateKey({
          provider: 'anthropic',
          apiKey: 'sk-ant-new-key-wxyz',
          passphrase: 'not the passphrase',
        });
      }),
    ).rejects.toThrow(WRONG_PASSPHRASE_MESSAGE);
    expect(result.current.getPlaintextKey('anthropic')).toBe(API_KEY);

    await act(async () => {
      await result.current.rotateKey({
        provider: 'anthropic',
        apiKey: 'sk-ant-new-key-wxyz',
        passphrase: PASSPHRASE,
      });
    });
    expect(result.current.getPlaintextKey('anthropic')).toBe('sk-ant-new-key-wxyz');
    expect(result.current.keys[0]).toMatchObject({ label: 'Personal key', last4: 'wxyz' });
  });

  it('rotateKey refuses a provider with no stored key', async () => {
    const { result } = await renderVault();
    await expect(
      act(async () => {
        await result.current.rotateKey({
          provider: 'anthropic',
          apiKey: API_KEY,
          passphrase: PASSPHRASE,
        });
      }),
    ).rejects.toThrow('no stored key for this provider');
  });

  it('removeKey deletes the record and returns to absent', async () => {
    const { result } = await renderVault();
    await addAnthropicKey(result);
    await act(async () => {
      await result.current.removeKey('anthropic');
    });
    expect(result.current.status('anthropic')).toBe('absent');
    expect(result.current.getPlaintextKey('anthropic')).toBeNull();
    expect(await getAllRecords()).toEqual([]);
  });

  it('manual lock clears plaintext access', async () => {
    const { result } = await renderVault();
    await addAnthropicKey(result);
    act(() => result.current.lock());
    expect(result.current.status('anthropic')).toBe('locked');
    expect(result.current.getPlaintextKey('anthropic')).toBeNull();
  });

  it('auto-locks 30 minutes after the last use', async () => {
    const { result } = await renderVault();
    // Fake only the timer functions: the auto-lock arms on setTimeout, while
    // fake-indexeddb and PBKDF2 keep their real (off-timer) scheduling.
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    await addAnthropicKey(result);
    expect(result.current.status('anthropic')).toBe('unlocked');

    act(() => {
      vi.advanceTimersByTime(AUTO_LOCK_MS);
    });
    expect(result.current.status('anthropic')).toBe('locked');
    expect(result.current.getPlaintextKey('anthropic')).toBeNull();
  });

  it('the auto-lock window slides on plaintext access', async () => {
    const { result } = await renderVault();
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    await addAnthropicKey(result);

    act(() => {
      vi.advanceTimersByTime(AUTO_LOCK_MS - 60_000);
    });
    // Using the key inside the window restarts the 30-minute clock…
    expect(result.current.getPlaintextKey('anthropic')).toBe(API_KEY);
    act(() => {
      vi.advanceTimersByTime(AUTO_LOCK_MS - 60_000);
    });
    expect(result.current.status('anthropic')).toBe('unlocked');
    // …and it still expires once the full window passes untouched.
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(result.current.status('anthropic')).toBe('locked');
  });

  it('useKeyVault throws outside the provider; the optional variant is null', () => {
    expect(() => renderHook(() => useKeyVault())).toThrow(
      'useKeyVault must be used inside <KeyVaultProvider>',
    );
    const { result } = renderHook(() => useKeyVaultOptional());
    expect(result.current).toBeNull();
  });
});
