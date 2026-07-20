import '@testing-library/jest-dom/vitest';
import { webcrypto } from 'node:crypto';

// jsdom 29 has no WebCrypto (crypto.subtle); the BYOK envelope crypto in
// src/vault/envelope.ts is pure crypto.subtle, so tests borrow Node's
// spec-compliant implementation.
if (!globalThis.crypto?.subtle) {
  Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true });
}

// jsdom 29 does not provide window.localStorage; the app relies on it for
// chat/theme/filter persistence, so give tests a spec-faithful in-memory one.
class MemoryStorage implements Storage {
  private store = new Map<string, string>();

  get length(): number {
    return this.store.size;
  }

  clear(): void {
    this.store.clear();
  }

  getItem(key: string): string | null {
    return this.store.has(key) ? this.store.get(key)! : null;
  }

  key(index: number): string | null {
    return [...this.store.keys()][index] ?? null;
  }

  removeItem(key: string): void {
    this.store.delete(key);
  }

  setItem(key: string, value: string): void {
    this.store.set(key, String(value));
  }
}

if (typeof window !== 'undefined' && !window.localStorage) {
  const storage = new MemoryStorage();
  Object.defineProperty(window, 'localStorage', { value: storage, configurable: true });
  Object.defineProperty(globalThis, 'localStorage', { value: storage, configurable: true });
}
