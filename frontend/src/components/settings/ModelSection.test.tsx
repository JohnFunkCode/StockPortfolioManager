/**
 * ModelSection tests (issue #124): renders the three catalog options, shows
 * the loaded value from GET /api/settings, and selecting one PUTs it.
 */
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import ModelSection from './ModelSection';
import * as settingsApi from '../../api/settings';

const SETTINGS = {
  chat_model: 'claude-sonnet-5',
  models: [
    { id: 'claude-sonnet-5', name: 'Claude Sonnet 5', description: 'Recommended daily driver.' },
    { id: 'claude-opus-4-8', name: 'Claude Opus 4.8', description: 'Heavy-lifting flagship.' },
    { id: 'claude-fable-5', name: 'Claude Fable 5', description: 'Most capable general model.' },
  ],
};

describe('ModelSection', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('loads the current model and offers the three catalog options', async () => {
    vi.spyOn(settingsApi, 'getSettings').mockResolvedValue(SETTINGS);
    render(<ModelSection />);

    expect(await screen.findByText('Claude Sonnet 5')).toBeTruthy();
    expect(screen.getByText('Recommended daily driver.')).toBeTruthy();

    const user = userEvent.setup();
    await user.click(screen.getByRole('combobox'));
    expect(screen.getAllByRole('option')).toHaveLength(3);
  });

  it('selecting a model calls putChatModel and updates the shown description', async () => {
    vi.spyOn(settingsApi, 'getSettings').mockResolvedValue(SETTINGS);
    const putSpy = vi.spyOn(settingsApi, 'putChatModel').mockResolvedValue({
      ...SETTINGS,
      chat_model: 'claude-opus-4-8',
    });
    render(<ModelSection />);
    await screen.findByText('Claude Sonnet 5');

    const user = userEvent.setup();
    await user.click(screen.getByRole('combobox'));
    await user.click(screen.getByRole('option', { name: 'Claude Opus 4.8' }));

    expect(putSpy).toHaveBeenCalledWith('claude-opus-4-8');
    expect(await screen.findByText('Heavy-lifting flagship.')).toBeTruthy();
  });
});
