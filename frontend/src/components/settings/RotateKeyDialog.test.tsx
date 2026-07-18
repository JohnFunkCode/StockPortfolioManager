/**
 * RotateKeyDialog flow tests (BYOK 5a): the new key is validated before the
 * stored record is touched, and a wrong passphrase or failed validation
 * leaves the old key material intact.
 */
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import type { ReactNode } from 'react';

import RotateKeyDialog from './RotateKeyDialog';
import { KeyVaultProvider } from '../../vault/KeyVaultContext';
import { unwrapKey, wrapKey } from '../../vault/vaultCrypto';
import { getAllRecords, putRecord } from '../../vault/vaultStore';
import { useValidateKey } from '../../hooks/useKeyProxy';

vi.mock('../../hooks/useKeyProxy', () => ({
  useValidateKey: vi.fn(),
}));

const OLD_KEY = 'sk-ant-old-key-wxyz';
const NEW_KEY = 'sk-ant-new-key-abcd';
const PASSPHRASE = 'correct horse battery staple';

const mutateAsync = vi.fn();
const useValidateKeyMock = vi.mocked(useValidateKey);

async function seedVault() {
  await putRecord({
    provider: 'anthropic',
    ...(await wrapKey(OLD_KEY, PASSPHRASE)),
    label: 'Personal key',
    last4: 'wxyz',
    createdAt: Date.now(),
  });
}

function renderDialog(onClose = vi.fn()) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <KeyVaultProvider>{children}</KeyVaultProvider>
  );
  render(<RotateKeyDialog open onClose={onClose} provider="anthropic" />, { wrapper });
  return onClose;
}

async function submit(apiKey: string, passphrase: string) {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText(/New API key/), apiKey);
  await user.type(screen.getByLabelText(/Vault passphrase/), passphrase);
  await user.click(screen.getByRole('button', { name: 'Rotate Key' }));
}

describe('RotateKeyDialog', () => {
  beforeEach(async () => {
    Object.defineProperty(globalThis, 'indexedDB', {
      value: new IDBFactory(),
      configurable: true,
    });
    await seedVault();
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

  it('validates the new key, swaps the material, and closes', async () => {
    const onClose = renderDialog();
    await submit(NEW_KEY, PASSPHRASE);

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(mutateAsync).toHaveBeenCalledWith({ provider: 'anthropic', apiKey: NEW_KEY });

    const [record] = await getAllRecords();
    expect(record.last4).toBe('abcd');
    expect(await unwrapKey(record, PASSPHRASE)).toBe(NEW_KEY);
    expect(JSON.stringify(record)).not.toContain(NEW_KEY);
  });

  it('a wrong passphrase keeps the old key and the dialog open', async () => {
    const onClose = renderDialog();
    await submit(NEW_KEY, 'not the passphrase');

    expect(
      await screen.findByText('Incorrect passphrase (or the stored key is corrupted).'),
    ).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();

    const [record] = await getAllRecords();
    expect(await unwrapKey(record, PASSPHRASE)).toBe(OLD_KEY);
  });

  it('a failed validation never touches the stored record', async () => {
    mutateAsync.mockRejectedValue(
      new Error('The provider rejected this key. Check that you pasted it completely.'),
    );
    const onClose = renderDialog();
    await submit(NEW_KEY, PASSPHRASE);

    expect(
      await screen.findByText(
        'The provider rejected this key. Check that you pasted it completely.',
      ),
    ).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();

    const [record] = await getAllRecords();
    expect(record.last4).toBe('wxyz');
    expect(await unwrapKey(record, PASSPHRASE)).toBe(OLD_KEY);
  });
});
