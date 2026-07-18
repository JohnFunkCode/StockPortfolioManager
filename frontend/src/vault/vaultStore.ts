/**
 * BYOK browser vault — IndexedDB persistence (Phase 4).
 *
 * Promise-wrapped IndexedDB: database `hl-keyvault`, object store `keys`
 * keyed by provider (one wrapped key per provider). Records hold only
 * passphrase-encrypted material plus display metadata (label, last4) — the
 * plaintext API key never touches this module.
 *
 * The `indexedDB` global is resolved at call time so tests can substitute
 * fake-indexeddb's IDBFactory per test.
 *
 * Never-log policy: nothing here logs, and no error message may carry record
 * contents — messages describe structure only.
 */

export const VAULT_DB_NAME = 'hl-keyvault';
export const VAULT_STORE_NAME = 'keys';
const VAULT_DB_VERSION = 1;

export interface VaultKdfParams {
  alg: 'PBKDF2-SHA256';
  iterations: number;
}

/** One passphrase-wrapped provider key; ct/iv/salt are b64url strings. */
export interface VaultRecord {
  provider: string;
  ct: string;
  iv: string;
  salt: string;
  kdf: VaultKdfParams;
  label: string;
  last4: string;
  createdAt: number;
}

/** Thrown for vault storage failures; never carries record contents. */
export class VaultStoreError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'VaultStoreError';
  }
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(VAULT_DB_NAME, VAULT_DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(VAULT_STORE_NAME)) {
        db.createObjectStore(VAULT_STORE_NAME, { keyPath: 'provider' });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(new VaultStoreError('could not open the key vault database'));
    request.onblocked = () => reject(new VaultStoreError('the key vault database is blocked by another tab'));
  });
}

/** Run one request in its own transaction; the db closes either way. */
async function withStore<T>(
  mode: IDBTransactionMode,
  operation: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  const db = await openDb();
  return new Promise<T>((resolve, reject) => {
    let request: IDBRequest<T>;
    try {
      const tx = db.transaction(VAULT_STORE_NAME, mode);
      tx.onabort = () => reject(new VaultStoreError('the key vault transaction was aborted'));
      request = operation(tx.objectStore(VAULT_STORE_NAME));
    } catch {
      db.close();
      reject(new VaultStoreError('the key vault operation could not start'));
      return;
    }
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(new VaultStoreError('the key vault operation failed'));
  }).finally(() => db.close());
}

/** Insert or overwrite the record for its provider. */
export async function putRecord(record: VaultRecord): Promise<void> {
  await withStore('readwrite', (store) => store.put(record));
}

export async function getRecord(provider: string): Promise<VaultRecord | null> {
  const result = await withStore<VaultRecord | undefined>('readonly', (store) => store.get(provider));
  return result ?? null;
}

export async function getAllRecords(): Promise<VaultRecord[]> {
  return withStore<VaultRecord[]>('readonly', (store) => store.getAll());
}

export async function deleteRecord(provider: string): Promise<void> {
  await withStore('readwrite', (store) => store.delete(provider));
}
