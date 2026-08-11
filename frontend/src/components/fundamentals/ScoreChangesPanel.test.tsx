import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';

import ScoreChangesPanel from './ScoreChangesPanel';
import { mockApi, renderWithProviders, scoreChange, scoreChanges } from '../../testUtils';

afterEach(() => {
  vi.unstubAllGlobals();
});

const CHANGES = '/api/securities/fundamentals/score-changes';

describe('ScoreChangesPanel', () => {
  it('shows a spinner while the request is in flight', () => {
    mockApi([[CHANGES, scoreChanges()]]);
    renderWithProviders(<ScoreChangesPanel />);
    expect(screen.getByText(/Comparing snapshots/i)).toBeInTheDocument();
  });

  it('shows an error alert when the fetch fails', async () => {
    mockApi([[CHANGES, { __status: 500, error: 'boom' }]]);
    renderWithProviders(<ScoreChangesPanel />);
    await waitFor(() =>
      expect(screen.getByText(/Couldn't load fundamental score changes/i)).toBeInTheDocument(),
    );
  });

  it('renders the delta and the then-to-now scores the server computed', async () => {
    mockApi([
      [
        CHANGES,
        scoreChanges([
          scoreChange('INTC', { delta: 12, score_then: 60, score_now: 72 }),
          scoreChange('WMT', {
            delta: -8,
            direction: 'deteriorating',
            score_then: 70,
            score_now: 62,
            label_then: 'strong',
            label_now: 'mixed',
          }),
        ]),
      ],
    ]);
    renderWithProviders(<ScoreChangesPanel />);
    await waitFor(() => expect(screen.getByTestId('score-changes-panel')).toBeInTheDocument());

    expect(screen.getByText('60 → 72')).toBeInTheDocument();
    expect(screen.getByText('70 → 62')).toBeInTheDocument();
    // The sign lives in the arrow, so the magnitude is unsigned.
    expect(screen.getByText('12')).toBeInTheDocument();
    expect(screen.getByText('8')).toBeInTheDocument();
    expect(screen.queryByText('-8')).not.toBeInTheDocument();
    expect(screen.getByLabelText('improving')).toBeInTheDocument();
    expect(screen.getByLabelText('deteriorating')).toBeInTheDocument();
  });

  it('surfaces the unmeasured count — a young cache is not a quiet market', async () => {
    mockApi([
      [
        CHANGES,
        scoreChanges([scoreChange('INTC')], { symbols_with_insufficient_history: 17 }),
      ],
    ]);
    renderWithProviders(<ScoreChangesPanel />);
    await waitFor(() => expect(screen.getByText('17 unmeasured')).toBeInTheDocument());
    expect(screen.getByText('1 movers')).toBeInTheDocument();
  });

  it('hides the unmeasured chip when every symbol had history', async () => {
    mockApi([[CHANGES, scoreChanges([scoreChange('INTC')])]]);
    renderWithProviders(<ScoreChangesPanel />);
    await waitFor(() => expect(screen.getByText('1 movers')).toBeInTheDocument());
    expect(screen.queryByText(/unmeasured/)).not.toBeInTheDocument();
  });

  it('says nothing moved rather than rendering an empty table', async () => {
    mockApi([[CHANGES, scoreChanges([], { min_delta: 5, since_days: 30 })]]);
    renderWithProviders(<ScoreChangesPanel />);
    await waitFor(() =>
      expect(screen.getByText(/No symbol moved 5 points or more/i)).toBeInTheDocument(),
    );
  });

  it('truncates to eight rows in the rail variant and drops the window column', async () => {
    const many = Array.from({ length: 11 }, (_, i) =>
      scoreChange(`SYM${i}`, { delta: 20 - i, score_now: 80 - i }),
    );
    mockApi([[CHANGES, scoreChanges(many)]]);
    renderWithProviders(<ScoreChangesPanel variant="rail" />);
    await waitFor(() => expect(screen.getByText('SYM0')).toBeInTheDocument());

    expect(screen.getByText('SYM7')).toBeInTheDocument();
    expect(screen.queryByText('SYM8')).not.toBeInTheDocument();
    // The count chip still reports every mover, not just the shown slice.
    expect(screen.getByText('11 movers')).toBeInTheDocument();
    expect(screen.queryByText('Window')).not.toBeInTheDocument();
  });

  it('sends the tracked scope on the wire', async () => {
    const api = mockApi([[CHANGES, scoreChanges()]]);
    renderWithProviders(<ScoreChangesPanel />);
    await waitFor(() => expect(screen.getByText('INTC')).toBeInTheDocument());
    expect(api.calls[0][0]).toContain('scope=tracked');
  });
});
