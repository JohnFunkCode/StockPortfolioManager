/**
 * Passphrase-strength minimum for vault creation (BYOK 5a).
 *
 * The IndexedDB blob is offline-brute-forceable at PBKDF2-600k if exfiltrated,
 * so weak passphrases are refused outright: a hard length floor plus a naive
 * character-class entropy estimate (length × log2(pool)). Deliberately simple
 * and explainable — the inline feedback is the product, not the estimator.
 */
export const MIN_PASSPHRASE_LENGTH = 12;
export const MIN_PASSPHRASE_BITS = 60;

export interface PassphraseStrength {
  ok: boolean;
  /** Estimated entropy in bits (0 for an empty passphrase). */
  bits: number;
  /** Inline feedback shown under the field. */
  feedback: string;
}

export function estimatePassphraseStrength(passphrase: string): PassphraseStrength {
  let pool = 0;
  if (/[a-z]/.test(passphrase)) pool += 26;
  if (/[A-Z]/.test(passphrase)) pool += 26;
  if (/[0-9]/.test(passphrase)) pool += 10;
  if (/[^a-zA-Z0-9]/.test(passphrase)) pool += 33;
  const bits = pool > 0 ? Math.round(passphrase.length * Math.log2(pool)) : 0;

  if (passphrase.length < MIN_PASSPHRASE_LENGTH) {
    return {
      ok: false,
      bits,
      feedback: `Use at least ${MIN_PASSPHRASE_LENGTH} characters — a few random words work well.`,
    };
  }
  if (bits < MIN_PASSPHRASE_BITS) {
    return {
      ok: false,
      bits,
      feedback: 'Add another word or more variety (mixed case, digits, symbols).',
    };
  }
  return { ok: true, bits, feedback: `Strong passphrase (~${bits} bits).` };
}
