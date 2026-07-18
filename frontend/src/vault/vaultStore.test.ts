/**
 * IndexedDB vault store tests (Phase 4) — fake-indexeddb per test, so every
 * test starts from an empty browser profile.
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';

import {
  deleteRecord,
  getAllRecords,
  getRecord,
  putRecord,
  type VaultRecord,
} from './vaultStore';

function makeRecord(overrides: Partial<VaultRecord> = {}): VaultRecord {
  return {
    provider: 'anthropic',
    ct: 'Y3QtYnl0ZXM',
    iv: 'aXYtYnl0ZXM',
    salt: 'c2FsdC1ieXRlcw',
    kdf: { alg: 'PBKDF2-SHA256', iterations: 600_000 },
    label: 'Personal key',
    last4: 'abcd',
    createdAt: 1_752_800_000_000,
    ...overrides,
  };
}

describe('vaultStore', () => {
  beforeEach(() => {
    Object.defineProperty(globalThis, 'indexedDB', {
      value: new IDBFactory(),
      configurable: true,
    });
  });

  it('round-trips a record by provider', async () => {
    const record = makeRecord();
    await putRecord(record);
    expect(await getRecord('anthropic')).toEqual(record);
  });

  it('returns null for a provider with no record', async () => {
    expect(await getRecord('anthropic')).toBeNull();
  });

  it('put overwrites the existing record for the same provider', async () => {
    await putRecord(makeRecord({ last4: 'abcd' }));
    await putRecord(makeRecord({ last4: 'wxyz' }));
    const all = await getAllRecords();
    expect(all).toHaveLength(1);
    expect(all[0].last4).toBe('wxyz');
  });

  it('getAllRecords returns every provider record', async () => {
    await putRecord(makeRecord({ provider: 'anthropic' }));
    await putRecord(makeRecord({ provider: 'openai', last4: 'wxyz' }));
    const providers = (await getAllRecords()).map((record) => record.provider).sort();
    expect(providers).toEqual(['anthropic', 'openai']);
  });

  it('deleteRecord removes only the named provider', async () => {
    await putRecord(makeRecord({ provider: 'anthropic' }));
    await putRecord(makeRecord({ provider: 'openai' }));
    await deleteRecord('anthropic');
    expect(await getRecord('anthropic')).toBeNull();
    expect(await getRecord('openai')).not.toBeNull();
  });

  it('deleteRecord on a missing provider is a no-op', async () => {
    await expect(deleteRecord('anthropic')).resolves.toBeUndefined();
  });
});
