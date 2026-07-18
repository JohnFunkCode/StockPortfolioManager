/** Passphrase-strength minimum tests (BYOK 5a). */
import { describe, expect, it } from 'vitest';

import {
  MIN_PASSPHRASE_BITS,
  MIN_PASSPHRASE_LENGTH,
  estimatePassphraseStrength,
} from './passphraseStrength';

describe('estimatePassphraseStrength', () => {
  it('rejects an empty passphrase with the length feedback', () => {
    const result = estimatePassphraseStrength('');
    expect(result.ok).toBe(false);
    expect(result.bits).toBe(0);
    expect(result.feedback).toContain(`at least ${MIN_PASSPHRASE_LENGTH} characters`);
  });

  it('rejects anything under the length floor regardless of variety', () => {
    const result = estimatePassphraseStrength('aA1!aA1!aA1');
    expect(result.ok).toBe(false);
    expect(result.feedback).toContain(`at least ${MIN_PASSPHRASE_LENGTH} characters`);
  });

  it('rejects 12 lowercase letters (56 bits) on the entropy floor', () => {
    const result = estimatePassphraseStrength('abcdefghijkl');
    expect(result.ok).toBe(false);
    expect(result.bits).toBeLessThan(MIN_PASSPHRASE_BITS);
    expect(result.feedback).toContain('Add another word or more variety');
  });

  it('accepts a multi-word passphrase well past both floors', () => {
    const result = estimatePassphraseStrength('correct horse battery staple');
    expect(result.ok).toBe(true);
    expect(result.bits).toBeGreaterThanOrEqual(MIN_PASSPHRASE_BITS);
    expect(result.feedback).toContain('Strong passphrase');
  });

  it('accepts a 12-char mixed-class passphrase (variety clears the entropy floor)', () => {
    const result = estimatePassphraseStrength('aB3$eF6&iJ9!');
    expect(result.ok).toBe(true);
  });
});
