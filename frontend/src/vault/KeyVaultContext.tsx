/**
 * BYOK browser vault — React state (Phase 4).
 *
 * Per-provider key lifecycle: absent → (add) → unlocked ⇄ locked → (remove)
 * → absent. One passphrase covers the whole vault: unlock opens every stored
 * provider key, and adding a key while others exist requires the same
 * passphrase (verified against an existing record before anything is
 * written).
 *
 * Plaintext keys live only in a ref — never React state, never storage — so
 * no render, devtools serialization, or persistence path ever carries them.
 * A 30-minute sliding auto-lock (reset by unlock/add and by every plaintext
 * read) clears the ref; `unlockedProviders` state mirrors just the *fact* of
 * unlockedness for rendering.
 *
 * Never-log policy: nothing here logs; errors surface to the caller with
 * constant messages from vaultCrypto/vaultStore.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { unwrapKey, wrapKey } from './vaultCrypto';
import {
  deleteRecord,
  getAllRecords,
  putRecord,
  type VaultRecord,
} from './vaultStore';

/** 30-minute sliding auto-lock (decided 2026-07-16; revisit after user testing). */
export const AUTO_LOCK_MS = 30 * 60 * 1000;

export type VaultKeyStatus = 'absent' | 'locked' | 'unlocked';

/** Display metadata for one stored provider key — no secret material. */
export interface VaultKeyMeta {
  provider: string;
  label: string;
  last4: string;
  createdAt: number;
  status: VaultKeyStatus;
}

interface AddKeyArgs {
  provider: string;
  apiKey: string;
  passphrase: string;
  label: string;
}

interface RotateKeyArgs {
  provider: string;
  apiKey: string;
  passphrase: string;
}

interface KeyVaultContextValue {
  /** One entry per stored provider key, lifecycle status included. */
  keys: VaultKeyMeta[];
  /** True once the initial IndexedDB read has completed. */
  ready: boolean;
  status: (provider: string) => VaultKeyStatus;
  /** Store a new provider key; the vault's passphrase when records already
   * exist, or the vault-creating passphrase when it's the first. Leaves the
   * provider unlocked. */
  addKey: (args: AddKeyArgs) => Promise<void>;
  /** Replace a provider's key material under the existing passphrase
   * (verified against the stored record before overwriting). */
  rotateKey: (args: RotateKeyArgs) => Promise<void>;
  removeKey: (provider: string) => Promise<void>;
  /** Open every stored key with the vault passphrase. */
  unlock: (passphrase: string) => Promise<void>;
  lock: () => void;
  /** Plaintext for an unlocked provider (null otherwise). Reading slides the
   * auto-lock window. Callers must not persist or log the value. */
  getPlaintextKey: (provider: string) => string | null;
}

const KeyVaultContext = createContext<KeyVaultContextValue | null>(null);

export function KeyVaultProvider({ children }: { children: ReactNode }) {
  const [records, setRecords] = useState<VaultRecord[]>([]);
  const [ready, setReady] = useState(false);
  const [unlockedProviders, setUnlockedProviders] = useState<string[]>([]);
  const plaintextRef = useRef<Map<string, string>>(new Map());
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let cancelled = false;
    getAllRecords()
      .then((stored) => {
        if (!cancelled) setRecords(stored);
      })
      .catch(() => {
        /* unreadable vault behaves as empty; adding a key rewrites it */
      })
      .finally(() => {
        if (!cancelled) setReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const lock = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    plaintextRef.current.clear();
    setUnlockedProviders([]);
  }, []);

  // Unmount is "tab close" in the lifecycle diagram: drop plaintext.
  useEffect(() => {
    const plaintext = plaintextRef.current;
    return () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current);
      plaintext.clear();
    };
  }, []);

  const armAutoLock = useCallback(() => {
    if (timerRef.current !== null) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(lock, AUTO_LOCK_MS);
  }, [lock]);

  /** One-passphrase rule: prove the given passphrase opens the vault. */
  const verifyPassphrase = useCallback(
    async (passphrase: string, against: VaultRecord) => {
      await unwrapKey(against, passphrase); // throws WRONG_PASSPHRASE_MESSAGE
    },
    [],
  );

  const addKey = useCallback(
    async ({ provider, apiKey, passphrase, label }: AddKeyArgs) => {
      const existing = records.find((record) => record.provider !== provider) ?? records[0];
      if (existing !== undefined) {
        await verifyPassphrase(passphrase, existing);
      }
      const wrapped = await wrapKey(apiKey, passphrase);
      const record: VaultRecord = {
        provider,
        ...wrapped,
        label,
        last4: apiKey.slice(-4),
        createdAt: Date.now(),
      };
      await putRecord(record);
      if (records.length === 0) {
        // First key = vault creation: ask the browser not to evict the store
        // (best effort — the key is per-browser either way; see plan tradeoff).
        try {
          await navigator.storage?.persist?.();
        } catch {
          /* ignore */
        }
      }
      setRecords((prev) => [...prev.filter((r) => r.provider !== provider), record]);
      plaintextRef.current.set(provider, apiKey);
      setUnlockedProviders((prev) => (prev.includes(provider) ? prev : [...prev, provider]));
      armAutoLock();
    },
    [records, verifyPassphrase, armAutoLock],
  );

  const rotateKey = useCallback(
    async ({ provider, apiKey, passphrase }: RotateKeyArgs) => {
      const current = records.find((record) => record.provider === provider);
      if (current === undefined) {
        throw new Error('no stored key for this provider; add one instead');
      }
      await verifyPassphrase(passphrase, current);
      const wrapped = await wrapKey(apiKey, passphrase);
      const record: VaultRecord = {
        provider,
        ...wrapped,
        label: current.label,
        last4: apiKey.slice(-4),
        createdAt: Date.now(),
      };
      await putRecord(record);
      setRecords((prev) => [...prev.filter((r) => r.provider !== provider), record]);
      plaintextRef.current.set(provider, apiKey);
      setUnlockedProviders((prev) => (prev.includes(provider) ? prev : [...prev, provider]));
      armAutoLock();
    },
    [records, verifyPassphrase, armAutoLock],
  );

  const removeKey = useCallback(async (provider: string) => {
    await deleteRecord(provider);
    plaintextRef.current.delete(provider);
    setRecords((prev) => prev.filter((record) => record.provider !== provider));
    setUnlockedProviders((prev) => prev.filter((p) => p !== provider));
  }, []);

  const unlock = useCallback(
    async (passphrase: string) => {
      if (records.length === 0) {
        throw new Error('the vault is empty; add a key instead');
      }
      // All-or-nothing: one passphrase opens every record, so any failure
      // (first record included) leaves the vault fully locked.
      const opened = new Map<string, string>();
      for (const record of records) {
        opened.set(record.provider, await unwrapKey(record, passphrase));
      }
      plaintextRef.current = opened;
      setUnlockedProviders([...opened.keys()]);
      armAutoLock();
    },
    [records, armAutoLock],
  );

  const status = useCallback(
    (provider: string): VaultKeyStatus => {
      if (!records.some((record) => record.provider === provider)) return 'absent';
      return unlockedProviders.includes(provider) ? 'unlocked' : 'locked';
    },
    [records, unlockedProviders],
  );

  const getPlaintextKey = useCallback(
    (provider: string): string | null => {
      const plaintext = plaintextRef.current.get(provider);
      if (plaintext === undefined) return null;
      armAutoLock(); // sliding window: every use extends the session
      return plaintext;
    },
    [armAutoLock],
  );

  const keys: VaultKeyMeta[] = records
    .map((record) => ({
      provider: record.provider,
      label: record.label,
      last4: record.last4,
      createdAt: record.createdAt,
      status: status(record.provider),
    }))
    .sort((a, b) => a.provider.localeCompare(b.provider));

  return (
    <KeyVaultContext.Provider
      value={{
        keys,
        ready,
        status,
        addKey,
        rotateKey,
        removeKey,
        unlock,
        lock,
        getPlaintextKey,
      }}
    >
      {children}
    </KeyVaultContext.Provider>
  );
}

export function useKeyVault(): KeyVaultContextValue {
  const value = useContext(KeyVaultContext);
  if (!value) throw new Error('useKeyVault must be used inside <KeyVaultProvider>');
  return value;
}

/** Like useKeyVault, but safe outside <KeyVaultProvider> — returns null so
 * components stay renderable in isolation/tests. */
export function useKeyVaultOptional(): KeyVaultContextValue | null {
  return useContext(KeyVaultContext);
}
