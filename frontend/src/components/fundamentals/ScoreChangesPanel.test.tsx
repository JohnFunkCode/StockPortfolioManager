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

describe('ScoreChangesPanel news cue (#433)', () => {
  const RULE = { mixed_net_band: 0.1, min_articles: 10, min_coverage: 0.25 };
  const news = (verdict: string, over: Record<string, unknown> = {}) => ({
    verdict, tone: 'negative', net_score: -0.382, article_count: 34,
    positive_count: 1, negative_count: 14, neutral_count: 19,
    days_covered: 23, window_days: 75, ...over,
  });

  function arm() {
    mockApi([[CHANGES, scoreChanges([
      scoreChange('WDC', { news: news('diverges') }),
      scoreChange('IREN', { delta: -7, direction: 'deteriorating', news: news('agrees') }),
      scoreChange('META', { news: news('mixed', { tone: 'mixed', net_score: 0.05 }) }),
      scoreChange('LTRX', { news: news('thin', { article_count: 6, net_score: 0.5 }) }),
      scoreChange('NIB.F', { news: news('none', {
        tone: null, net_score: null, article_count: 0, positive_count: 0,
        negative_count: 0, neutral_count: 0 }) }),
      scoreChange('OLD'),
    ], { news_rule: RULE })]]);
  }

  it('shows each verdict the server sent, worded and coloured, never recomputed', async () => {
    arm();
    renderWithProviders(<ScoreChangesPanel />);
    await waitFor(() => expect(screen.getByText('News')).toBeInTheDocument());

    expect(screen.getByTestId('news-cue-WDC')).toHaveTextContent('diverges');
    expect(screen.getByTestId('news-cue-IREN')).toHaveTextContent('agrees');
    expect(screen.getByTestId('news-cue-META')).toHaveTextContent('mixed');
    expect(screen.getByTestId('news-cue-LTRX')).toHaveTextContent('thin');
    expect(screen.getByTestId('news-cue-NIB.F')).toHaveTextContent('—');
    // Diverges is amber (look closer), agrees green — both the ink shades.
    expect(screen.getByTestId('news-cue-WDC')).toHaveStyle({ color: 'var(--qc-ink-amber)' });
    expect(screen.getByTestId('news-cue-IREN')).toHaveStyle({ color: 'var(--qc-ink-green)' });
  });

  it('states the counts, the coverage and the rule behind a verdict', async () => {
    arm();
    renderWithProviders(<ScoreChangesPanel />);
    const cue = await screen.findByTestId('news-cue-WDC');
    const label = cue.getAttribute('aria-label') ?? '';
    expect(label).toContain('News net −0.38 over 34 articles (1↑ 14↓ 19–)');
    expect(label).toContain("news history covers 23 of this move's 75 days");
    expect(label).toContain('A verdict needs 10+ articles and 25% of the window covered');
    expect(label).toContain('±0.10 is mixed');
    expect(screen.getByTestId('news-cue-NIB.F').getAttribute('aria-label'))
      .toContain("No scored articles in the move's window");
  });

  it('keeps the cue in the rail, and a server without news shows a dash', async () => {
    arm();
    renderWithProviders(<ScoreChangesPanel variant="rail" />);
    expect(await screen.findByTestId('news-cue-WDC')).toHaveTextContent('diverges');
    expect(screen.queryByTestId('news-cue-OLD')).toBeNull();
    const oldRow = screen.getByText('OLD').closest('tr')!;
    expect(oldRow).toHaveTextContent('—');
  });
});
