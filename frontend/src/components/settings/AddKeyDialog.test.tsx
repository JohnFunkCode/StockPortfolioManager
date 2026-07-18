/**
 * AddKeyDialog flow tests (BYOK 5a): weak passphrases are refused before any
 * network call, validation failure keeps the dialog open with nothing stored,
 * and success stores only ciphertext (never-log) and closes.
 *
 * The key-proxy hook is mocked (its real crypto is covered in
 * hooks/useKeyProxy.test.ts); the vault underneath is real, on a fresh
 * fake-indexeddb per test.
 */
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import type { ReactNode } from 'react';

import AddKeyDialog from './AddKeyDialog';
import { KeyVaultProvider } from '../../vault/KeyVaultContext';
import { wrapKey } from '../../vault/vaultCrypto';
import { getAllRecords, putRecord } from '../../vault/vaultStore';
import { useValidateKey } from '../../hooks/useKeyProxy';

vi.mock('../../hooks/useKeyProxy', () => ({
  useValidateKey: vi.fn(),
}));

const API_KEY = 'sk-ant-test-key-abcd';
const PASSPHRASE = 'correct horse battery staple';

const mutateAsync = vi.fn();
const useValidateKeyMock = vi.mocked(useValidateKey);

function renderDialog(onClose = vi.fn()) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <KeyVaultProvider>{children}</KeyVaultProvider>
  );
  render(<AddKeyDialog open onClose={onClose} />, { wrapper });
  return onClose;
}

async function fillForm(fields: { apiKey?: string; passphrase?: string; confirm?: string }) {
  const user = userEvent.setup();
  if (fields.apiKey) await user.type(screen.getByLabelText(/^API key/), fields.apiKey);
  if (fields.passphrase) {
    await user.type(screen.getByLabelText(/Vault passphrase/), fields.passphrase);
  }
  if (fields.confirm) {
    await user.type(screen.getByLabelText(/Confirm passphrase/), fields.confirm);
  }
  return user;
}

describe('AddKeyDialog', () => {
  beforeEach(() => {
    Object.defineProperty(globalThis, 'indexedDB', {
      value: new IDBFactory(),
      configurable: true,
    });
    mutateAsync.mockReset().mockResolvedValue({
      valid: true,
      provider: 'anthropic',
      key_hint: '…abcd',
    });
    useValidateKeyMock.mockReturnValue({
      mutateAsync,
      reset: vi.fn(),
    } as unknown as ReturnType<typeof useValidateKey>);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('refuses a weak passphrase before any API call and stores nothing', async () => {
    renderDialog();
    const user = await fillForm({
      apiKey: API_KEY,
      passphrase: 'abcdefghijkl',
      confirm: 'abcdefghijkl',
    });
    await user.click(screen.getByRole('button', { name: 'Validate & Save' }));

    // Shown twice: as live helper text under the field and as the submit error.
    expect(
      await screen.findAllByText('Add another word or more variety (mixed case, digits, symbols).'),
    ).toHaveLength(2);
    expect(mutateAsync).not.toHaveBeenCalled();
    expect(await getAllRecords()).toEqual([]);
  });

  it('refuses mismatched passphrases without calling the API', async () => {
    renderDialog();
    const user = await fillForm({
      apiKey: API_KEY,
      passphrase: PASSPHRASE,
      confirm: 'correct horse battery stable',
    });
    await user.click(screen.getByRole('button', { name: 'Validate & Save' }));

    expect(await screen.findByText('Passphrases do not match.')).toBeTruthy();
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  it('validates, stores ciphertext only, and closes on success', async () => {
    const onClose = renderDialog();
    const user = await fillForm({
      apiKey: API_KEY,
      passphrase: PASSPHRASE,
      confirm: PASSPHRASE,
    });
    await user.click(screen.getByRole('button', { name: 'Validate & Save' }));

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(mutateAsync).toHaveBeenCalledWith({ provider: 'anthropic', apiKey: API_KEY });

    const records = await getAllRecords();
    expect(records).toHaveLength(1);
    expect(records[0]).toMatchObject({
      provider: 'anthropic',
      label: 'Personal key',
      last4: 'abcd',
    });
    // Never-log: what persists is ciphertext, not the key or passphrase.
    expect(JSON.stringify(records[0])).not.toContain(API_KEY);
    expect(JSON.stringify(records[0])).not.toContain(PASSPHRASE);
  });

  it('keeps the dialog open with the error and stores nothing when validation fails', async () => {
    mutateAsync.mockRejectedValue(
      new Error('The provider rejected this key. Check that you pasted it completely.'),
    );
    const onClose = renderDialog();
    const user = await fillForm({
      apiKey: API_KEY,
      passphrase: PASSPHRASE,
      confirm: PASSPHRASE,
    });
    await user.click(screen.getByRole('button', { name: 'Validate & Save' }));

    expect(
      await screen.findByText(
        'The provider rejected this key. Check that you pasted it completely.',
      ),
    ).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
    expect(await getAllRecords()).toEqual([]);
  });

  it('with an existing vault asks only for the vault passphrase and enforces it', async () => {
    await putRecord({
      provider: 'anthropic',
      ...(await wrapKey('sk-ant-old-key-wxyz', PASSPHRASE)),
      label: 'Personal key',
      last4: 'wxyz',
      createdAt: Date.now(),
    });
    renderDialog();

    // The stored key loads async; the dialog then switches out of vault-creation mode.
    await waitFor(() =>
      expect(screen.getByLabelText(/Existing vault passphrase/)).toBeTruthy(),
    );
    expect(screen.queryByLabelText(/Confirm passphrase/)).toBeNull();

    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/^API key/), API_KEY);
    await user.type(screen.getByLabelText(/Existing vault passphrase/), 'not the passphrase');
    await user.click(screen.getByRole('button', { name: 'Validate & Save' }));

    expect(
      await screen.findByText('Incorrect passphrase (or the stored key is corrupted).'),
    ).toBeTruthy();
    // The old record is untouched.
    const records = await getAllRecords();
    expect(records).toHaveLength(1);
    expect(records[0].last4).toBe('wxyz');
  });
});
