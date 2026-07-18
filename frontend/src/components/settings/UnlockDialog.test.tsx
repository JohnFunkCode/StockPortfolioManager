/**
 * UnlockDialog tests (BYOK 5a): the right passphrase unlocks and closes; a
 * wrong one shows the constant vault error and stays open. Real vault + real
 * crypto on a fresh fake-indexeddb.
 */
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import type { ReactNode } from 'react';

import UnlockDialog from './UnlockDialog';
import { KeyVaultProvider, useKeyVault } from '../../vault/KeyVaultContext';
import { wrapKey } from '../../vault/vaultCrypto';
import { putRecord } from '../../vault/vaultStore';

const PASSPHRASE = 'correct horse battery staple';

/** Exposes the vault status alongside the dialog under test. */
function StatusProbe() {
  const { status } = useKeyVault();
  return <span data-testid="status">{status('anthropic')}</span>;
}

function renderDialog(onClose = vi.fn()) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <KeyVaultProvider>{children}</KeyVaultProvider>
  );
  render(
    <>
      <UnlockDialog open onClose={onClose} />
      <StatusProbe />
    </>,
    { wrapper },
  );
  return onClose;
}

describe('UnlockDialog', () => {
  beforeEach(async () => {
    Object.defineProperty(globalThis, 'indexedDB', {
      value: new IDBFactory(),
      configurable: true,
    });
    await putRecord({
      provider: 'anthropic',
      ...(await wrapKey('sk-ant-test-key-abcd', PASSPHRASE)),
      label: 'Personal key',
      last4: 'abcd',
      createdAt: Date.now(),
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('unlocks the vault and closes on the right passphrase', async () => {
    const onClose = renderDialog();
    await waitFor(() => expect(screen.getByTestId('status').textContent).toBe('locked'));

    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/Vault passphrase/), PASSPHRASE);
    await user.click(screen.getByRole('button', { name: 'Unlock' }));

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId('status').textContent).toBe('unlocked');
  });

  it('shows the constant error and stays open on a wrong passphrase', async () => {
    const onClose = renderDialog();
    await waitFor(() => expect(screen.getByTestId('status').textContent).toBe('locked'));

    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/Vault passphrase/), 'not the passphrase');
    await user.click(screen.getByRole('button', { name: 'Unlock' }));

    expect(
      await screen.findByText('Incorrect passphrase (or the stored key is corrupted).'),
    ).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByTestId('status').textContent).toBe('locked');
  });
});
