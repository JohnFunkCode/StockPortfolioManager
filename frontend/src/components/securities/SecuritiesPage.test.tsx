import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';

import SecuritiesPage from './SecuritiesPage';
import { mockApi, renderWithProviders, securityRow, type MockedApi } from '../../testUtils';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

const SECURITIES = {
  securities: [
    securityRow('INTC', {
      source: 'portfolio',
      tags: ['chips'],
      purchase_price: 31.5,
      quantity: 200,
    }),
    securityRow('WMT', { source: 'watchlist', tags: ['retail'] }),
    securityRow('NVDA', { source: 'both', tags: ['chips', 'ai', 'gpu', 'mega', 'index'] }),
  ],
};

const SENTIMENT = {
  count: 2,
  items: [
    {
      symbol: 'INTC', name: 'INTC Corp', overall_sentiment: 'negative',
      positive_count: 1, negative_count: 4, neutral_count: 2, scored_count: 7,
      captured_at: '2026-07-25T12:00:00Z',
    },
    {
      symbol: 'NVDA', name: 'NVDA Corp', overall_sentiment: 'positive',
      positive_count: 6, negative_count: 0, neutral_count: 1, scored_count: 7,
      captured_at: null,
    },
  ],
};

function arm(overrides: Array<[string | RegExp, unknown]> = []): MockedApi {
  return mockApi([
    ...(overrides as Array<[string | RegExp, never]>),
    ['/api/securities/screen', { count: 0, results: [] }],
    ['/news/sentiment-summary', SENTIMENT],
    ['/api/securities', SECURITIES],
  ]);
}

/**
 * MUI's Collapse keeps the sentiment table mounted while closed, so symbols
 * appear in both it and the DataGrid. Every assertion is scoped to one or the
 * other rather than searching the whole document.
 */
function inGrid() {
  return within(screen.getByRole('grid'));
}

function inSentimentTable() {
  return within(document.querySelector('table') as HTMLElement);
}

/** Render, then wait for the grid's first row. */
async function mount(overrides: Array<[string | RegExp, unknown]> = []) {
  const api = arm(overrides);
  renderWithProviders(<SecuritiesPage />);
  await waitFor(() => expect(inGrid().getByText('INTC')).toBeInTheDocument());
  return api;
}

beforeEach(() => localStorage.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  mockNavigate.mockClear();
});

describe('SecuritiesPage', () => {
  it('renders the securities in a grid', async () => {
    await mount();
    expect(inGrid().getByText('WMT')).toBeInTheDocument();
    expect(inGrid().getByText('NVDA')).toBeInTheDocument();
  });

  it('filters the grid by the search box', async () => {
    await mount();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'WMT' } });
    await waitFor(() => expect(inGrid().queryByText('INTC')).toBeNull());
    expect(inGrid().getByText('WMT')).toBeInTheDocument();
  });

  it('filters by source with the toggle group', async () => {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: 'Portfolio' }));
    await waitFor(() => expect(inGrid().queryByText('WMT')).toBeNull());
    // 'both' counts as portfolio too.
    expect(inGrid().getByText('INTC')).toBeInTheDocument();
    expect(inGrid().getByText('NVDA')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Both' }));
    await waitFor(() => expect(inGrid().queryByText('INTC')).toBeNull());
    expect(inGrid().getByText('NVDA')).toBeInTheDocument();
  });

  it('surfaces a load error', async () => {
    mockApi([
      ['/api/securities', () => ({ __status: 500, error: 'securities down' })],
      ['/news/sentiment-summary', SENTIMENT],
    ]);
    renderWithProviders(<SecuritiesPage />);
    await waitFor(() => expect(screen.getByText(/securities down/i)).toBeInTheDocument());
  });

  describe('grid cells', () => {
    it('formats cost basis and quantity, dashing out the blanks', async () => {
      await mount();
      expect(inGrid().getByText('$31.50')).toBeInTheDocument();
      expect(inGrid().getByText('200')).toBeInTheDocument();
      // WMT and NVDA carry neither, so both columns fall back to an em dash.
      expect(inGrid().getAllByText('—').length).toBeGreaterThanOrEqual(4);
    });

    it('renders a sentiment badge per row from the summary map', async () => {
      await mount();
      expect(inGrid().getByText('↑ pos')).toBeInTheDocument();
      expect(inGrid().getByText('↓ neg')).toBeInTheDocument();
    });

    it('caps the tag chips and rolls the rest into an overflow chip', async () => {
      await mount();
      // NVDA has 5 tags: 4 chips + "+1".
      expect(inGrid().getByText('+1')).toBeInTheDocument();
    });

    it('navigates from the symbol cell', async () => {
      await mount();
      fireEvent.click(inGrid().getByText('INTC'));
      expect(mockNavigate).toHaveBeenCalledWith('/securities/INTC');
    });
  });

  describe('tag filtering', () => {
    it('toggles a tag on from a grid chip and persists it', async () => {
      await mount();
      fireEvent.click(inGrid().getAllByText('retail')[0]);
      await waitFor(() => expect(inGrid().queryByText('INTC')).toBeNull());
      expect(JSON.parse(localStorage.getItem('securities-tag-filter') ?? '[]')).toEqual(['retail']);
    });

    it('toggles the same tag back off', async () => {
      await mount();
      fireEvent.click(inGrid().getAllByText('retail')[0]);
      await waitFor(() => expect(inGrid().queryByText('INTC')).toBeNull());
      fireEvent.click(inGrid().getAllByText('retail')[0]);
      await waitFor(() => expect(inGrid().getByText('INTC')).toBeInTheDocument());
    });

    it('restores a persisted tag filter on mount', async () => {
      localStorage.setItem('securities-tag-filter', JSON.stringify(['retail']));
      arm();
      renderWithProviders(<SecuritiesPage />);
      await waitFor(() => expect(inGrid().getByText('WMT')).toBeInTheDocument());
      expect(inGrid().queryByText('INTC')).toBeNull();
    });

    it('ignores a corrupt persisted filter instead of crashing', async () => {
      localStorage.setItem('securities-tag-filter', 'not json');
      await mount();
      expect(inGrid().getByText('WMT')).toBeInTheDocument();
    });

    it('clears all tags from the toolbar button', async () => {
      localStorage.setItem('securities-tag-filter', JSON.stringify(['retail']));
      arm();
      renderWithProviders(<SecuritiesPage />);
      await waitFor(() => expect(inGrid().getByText('WMT')).toBeInTheDocument());

      fireEvent.click(screen.getByRole('button', { name: /Clear Tags \(1\)/i }));
      await waitFor(() => expect(inGrid().getByText('INTC')).toBeInTheDocument());
      expect(JSON.parse(localStorage.getItem('securities-tag-filter') ?? 'null')).toEqual([]);
    });
  });

  describe('screener panel', () => {
    it('runs a preset and lists the matches', async () => {
      const api = await mount([
        ['/api/securities/screen', {
          count: 1,
          results: [{ symbol: 'INTC', rsi: 28.4, last_close: 31.12, news_sentiment: 'negative' }],
        }],
      ]);
      fireEvent.click(screen.getByRole('button', { name: /Screener/i }));
      fireEvent.click(screen.getByText('Oversold (RSI < 30)'));

      await waitFor(() =>
        expect(api.calls.some(([url]) => url.includes('rsi_max=30'))).toBe(true),
      );
      expect(await screen.findByText(/1 match —/)).toBeInTheDocument();
      expect(screen.getByText(/INTC · negative · RSI 28 · \$31\.12/)).toBeInTheDocument();
    });

    it('navigates from a screener result chip', async () => {
      await mount([
        ['/api/securities/screen', { count: 1, results: [{ symbol: 'INTC', rsi: 28, last_close: 31 }] }],
      ]);
      fireEvent.click(screen.getByRole('button', { name: /Screener/i }));
      fireEvent.click(screen.getByText('Oversold (RSI < 30)'));
      const chip = await screen.findByText(/^INTC · RSI/);
      fireEvent.click(chip);
      expect(mockNavigate).toHaveBeenCalledWith('/securities/INTC');
    });

    it('clicking the active preset again clears it', async () => {
      await mount();
      fireEvent.click(screen.getByRole('button', { name: /Screener/i }));
      fireEvent.click(screen.getByText('Overbought (RSI > 70)'));
      expect(await screen.findByText('Clear')).toBeInTheDocument();
      fireEvent.click(screen.getByText('Overbought (RSI > 70)'));
      await waitFor(() => expect(screen.queryByText('Clear')).toBeNull());
    });

    it('the Clear chip resets the active preset', async () => {
      await mount();
      fireEvent.click(screen.getByRole('button', { name: /Screener/i }));
      fireEvent.click(screen.getByText('MACD Bullish'));
      fireEvent.click(await screen.findByText('Clear'));
      await waitFor(() => expect(screen.queryByText('Clear')).toBeNull());
    });

    it('pluralises the match count', async () => {
      await mount([
        ['/api/securities/screen', {
          count: 2,
          results: [{ symbol: 'INTC' }, { symbol: 'WMT' }],
        }],
      ]);
      fireEvent.click(screen.getByRole('button', { name: /Screener/i }));
      fireEvent.click(screen.getByText('Near BB Lower'));
      expect(await screen.findByText(/2 matches —/)).toBeInTheDocument();
    });
  });

  describe('sentiment dashboard', () => {
    it('shows the ranked table with per-sentiment counts', async () => {
      await mount();
      fireEvent.click(screen.getByRole('button', { name: /^Sentiment$/i }));

      expect(await screen.findByText('2 scored securities')).toBeInTheDocument();
      expect(screen.getByText('↓ 1 negative')).toBeInTheDocument();
      expect(screen.getByText('↑ 1 positive')).toBeInTheDocument();
      expect(screen.getByText('– 0 neutral')).toBeInTheDocument();
      // captured_at renders as a bare date, and null falls back to a dash.
      expect(screen.getByText('2026-07-25')).toBeInTheDocument();
    });

    it('navigates from a sentiment table row', async () => {
      await mount();
      fireEvent.click(screen.getByRole('button', { name: /^Sentiment$/i }));
      const row = inSentimentTable().getByText('INTC Corp').closest('tr') as HTMLElement;
      fireEvent.click(row);
      expect(mockNavigate).toHaveBeenCalledWith('/securities/INTC');
    });

    it('refetches on demand', async () => {
      const api = await mount();
      fireEvent.click(screen.getByRole('button', { name: /^Sentiment$/i }));
      const before = api.calls.filter(([u]) => u.includes('sentiment-summary')).length;
      fireEvent.click(await screen.findByRole('button', { name: /Refresh$/i }));
      await waitFor(() =>
        expect(api.calls.filter(([u]) => u.includes('sentiment-summary')).length)
          .toBeGreaterThan(before),
      );
    });

    it('explains an empty dashboard rather than rendering a blank table', async () => {
      await mount([['/news/sentiment-summary', { count: 0, items: [] }]]);
      fireEvent.click(screen.getByRole('button', { name: /^Sentiment$/i }));
      expect(await screen.findByText(/No sentiment data yet/i)).toBeInTheDocument();
    });
  });

  describe('options snapshot refresh', () => {
    it('collects ATM snapshots for every source and reports the result', async () => {
      const api = await mount([
        ['/refresh-options-snapshots', { total: 12, succeeded: 11, failed: 1, duration_seconds: 42 }],
      ]);
      fireEvent.click(screen.getByRole('button', { name: /Refresh All \(ATM\)/i }));

      await waitFor(() =>
        expect(api.calls.some(([url]) =>
          url.includes('source=all') && url.includes('chain_type=atm'))).toBe(true),
      );
      expect(await screen.findByText(/11\/12 succeeded in 42s/)).toBeInTheDocument();
      expect(screen.getByText(/· 1 failed/)).toBeInTheDocument();
    });

    it('collects full chains for the portfolio only', async () => {
      const api = await mount([
        ['/refresh-options-snapshots', { total: 3, succeeded: 3, failed: 0, duration_seconds: 9 }],
      ]);
      fireEvent.click(screen.getByRole('button', { name: /Refresh Portfolio \(Full Chain\)/i }));

      await waitFor(() =>
        expect(api.calls.some(([url]) =>
          url.includes('source=portfolio') && url.includes('chain_type=full'))).toBe(true),
      );
      expect(await screen.findByText(/3\/3 succeeded in 9s/)).toBeInTheDocument();
      // No failures, so no failure clause.
      expect(screen.queryByText(/failed/)).toBeNull();
    });

    it('surfaces a refresh failure in a dismissable alert', async () => {
      await mount([
        ['/refresh-options-snapshots', () => ({ __status: 500, error: 'yahoo unavailable' })],
      ]);
      fireEvent.click(screen.getByRole('button', { name: /Refresh All \(ATM\)/i }));

      const alert = await screen.findByRole('alert');
      expect(within(alert).getByText(/yahoo unavailable/i)).toBeInTheDocument();
      fireEvent.click(within(alert).getByRole('button'));
      await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
    });
  });

  it('persists the grid sort model', async () => {
    await mount();
    fireEvent.click(inGrid().getByText('Name'));
    await waitFor(() =>
      expect(JSON.parse(localStorage.getItem('securities-sort-model') ?? '[]')).toHaveLength(1),
    );
  });

  it('restores a persisted sort model, ignoring corrupt values', async () => {
    localStorage.setItem('securities-sort-model', '{{{');
    await mount();
    expect(inGrid().getByText('INTC')).toBeInTheDocument();
  });

  it('opens the Add Security dialog', async () => {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: /Add Security/i }));
    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument());
  });
});
