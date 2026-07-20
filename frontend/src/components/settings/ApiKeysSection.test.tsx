/**
 * ApiKeysSection tests (BYOK 5a): the provider row's three states (absent /
 * locked / unlocked) and the confirm-guarded remove flow.
 */
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import type { ReactNode } from 'react';

import ApiKeysSection from './ApiKeysSection';
import { KeyVaultProvider } from '../../vault/KeyVaultContext';
import { wrapKey } from '../../vault/vaultCrypto';
import { getAllRecords, putRecord } from '../../vault/vaultStore';

vi.mock('../../hooks/useKeyProxy', () => ({
  useValidateKey: () => ({ mutateAsync: vi.fn(), reset: vi.fn() }),
}));

const PASSPHRASE = 'correct horse battery staple';

async function seedVault() {
  await putRecord({
    provider: 'anthropic',
    ...(await wrapKey('sk-ant-test-key-abcd', PASSPHRASE)),
    label: 'Personal key',
    last4: 'abcd',
    createdAt: Date.now(),
  });
}

function renderSection() {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <KeyVaultProvider>{children}</KeyVaultProvider>
  );
  render(<ApiKeysSection />, { wrapper });
}

describe('ApiKeysSection', () => {
  beforeEach(() => {
    Object.defineProperty(globalThis, 'indexedDB', {
      value: new IDBFactory(),
      configurable: true,
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('shows the empty state with an Add Key button', async () => {
    renderSection();
    expect(await screen.findByText('No key stored')).toBeTruthy();
    expect(screen.getByRole('button', { name: /Add Key/ })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Rotate' })).toBeNull();
    expect(screen.queryByRole('button', { name: /Unlock/ })).toBeNull();
  });

  it('shows a stored key as locked with its label, last4, and actions', async () => {
    await seedVault();
    renderSection();

    expect(await screen.findByText('••••abcd')).toBeTruthy();
    expect(screen.getByText('Personal key')).toBeTruthy();
    expect(screen.getByText('Locked')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Rotate' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Remove' })).toBeTruthy();
    expect(screen.getByRole('button', { name: /Unlock/ })).toBeTruthy();
  });

  it('unlocking via the vault Unlock button flips the chip and offers Lock', async () => {
    await seedVault();
    renderSection();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /Unlock/ }));
    const dialog = screen.getByRole('dialog');
    await user.type(within(dialog).getByLabelText(/Vault passphrase/), PASSPHRASE);
    await user.click(within(dialog).getByRole('button', { name: 'Unlock' }));

    expect(await screen.findByText('Unlocked')).toBeTruthy();
    // The closing dialog keeps the page aria-hidden until its exit transition ends.
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await user.click(screen.getByRole('button', { name: /^Lock$/ }));
    expect(await screen.findByText('Locked')).toBeTruthy();
  });

  it('remove asks for confirmation naming the provider, then deletes', async () => {
    await seedVault();
    renderSection();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Remove' }));
    expect(
      await screen.findByText(/Remove the stored Anthropic key from this browser\?/),
    ).toBeTruthy();

    await user.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Remove' }),
    );
    await waitFor(async () => expect(await getAllRecords()).toEqual([]));
    expect(await screen.findByText('No key stored')).toBeTruthy();
  });

  it('cancelling the remove confirmation keeps the key', async () => {
    await seedVault();
    renderSection();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Remove' }));
    await screen.findByText(/Remove the stored Anthropic key/);
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(await getAllRecords()).toHaveLength(1);
    expect(screen.getByText('••••abcd')).toBeTruthy();
  });
});
