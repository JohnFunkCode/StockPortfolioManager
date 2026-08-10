/**
 * Page suite for /watchlist (issue #147 Part C) — the report replacement.
 *
 * Two of these are regression guards rather than coverage: the currency-aware
 * price/cap cells, and the nulls-last comparator. Both encode the same lesson —
 * a number in the wrong currency, or a null sorted as if it were small, reads
 * as an answer.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';

import WatchlistPage from './WatchlistPage';
import {
  mockApi,
  renderWithProviders,
  watchlistFundamentals,
  watchlistRow,
  type MockedApi,
} from '../../testUtils';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

const ROWS = [
  watchlistRow('INTC', {
    tags: ['chips'],
    price: 31.5,
    composite_score: 72,
    fundamental_label: 'strong',
    return_30d: 4.8,
    market_cap: 140_000_000_000,
    market_cap_currency: 'USD',
    market_cap_usd: 140_000_000_000,
  }),
  watchlistRow('WMT', {
    tags: ['retail'],
    price: 92.25,
    composite_score: 61,
    fundamental_label: 'mixed',
    return_30d: -1.5,
    fundamentals_stale: true,
    fundamentals_age_hours: 220,
  }),
  // A Stockholm listing: priced in SEK, and market_cap_usd is null until an FX
  // source lands (issue #185).
  watchlistRow('ASSA-B.ST', {
    name: 'Assa Abloy',
    currency: 'SEK',
    tags: ['industrials', 'europe', 'locks', 'compounder', 'extra'],
    price: 310,
    composite_score: null,
    fundamental_label: null,
    return_30d: null,
    market_cap: 340_000_000_000,
    market_cap_currency: 'SEK',
    market_cap_usd: null,
    fundamentals_cached_at: null,
    fundamentals_stale: null,
    fundamentals_age_hours: null,
  }),
];

function arm(overrides: Array<[string | RegExp, unknown]> = []): MockedApi {
  return mockApi([
    ...(overrides as Array<[string | RegExp, never]>),
    ['/api/watchlist/fundamentals', watchlistFundamentals(ROWS)],
  ]);
}

function inGrid() {
  return within(screen.getByRole('grid'));
}

/** The symbol column top to bottom — what a sort is actually about. */
function symbolOrder(): (string | undefined)[] {
  return Array.from(document.querySelectorAll('.MuiDataGrid-row')).map(
    (r) => r.querySelector('[data-field="symbol"]')?.textContent ?? undefined,
  );
}

async function mount(overrides: Array<[string | RegExp, unknown]> = []) {
  const api = arm(overrides);
  renderWithProviders(<WatchlistPage />);
  await waitFor(() => expect(inGrid().getByText('INTC')).toBeInTheDocument());
  return api;
}

beforeEach(() => localStorage.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  mockNavigate.mockClear();
});

describe('WatchlistPage', () => {
  it('shows a spinner while the table is in flight', () => {
    arm();
    renderWithProviders(<WatchlistPage />);
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });

  it('surfaces a load error instead of an empty table', async () => {
    mockApi([['/api/watchlist/fundamentals', { __status: 500, error: 'watchlist down' }]]);
    renderWithProviders(<WatchlistPage />);
    await waitFor(() => expect(screen.getByText(/watchlist down/i)).toBeInTheDocument());
    expect(screen.queryByRole('grid')).not.toBeInTheDocument();
  });

  it('renders every row from a single request', async () => {
    const api = await mount();
    expect(inGrid().getByText('WMT')).toBeInTheDocument();
    expect(inGrid().getByText('ASSA-B.ST')).toBeInTheDocument();
    // One fetch for the whole page — not one per symbol.
    expect(api.calls).toHaveLength(1);
  });

  it('summarises count, price date, stale and unscored in the header', async () => {
    await mount();
    expect(screen.getByText('3 securities')).toBeInTheDocument();
    expect(screen.getByText('1 stale')).toBeInTheDocument();
    expect(screen.getByText('1 unscored')).toBeInTheDocument();
    expect(screen.getByText(/prices Aug 7, 2026/)).toBeInTheDocument();
  });

  describe('grid cells', () => {
    it('prices a foreign listing in its own currency, not dollars', async () => {
      await mount();
      expect(inGrid().getByText('$31.50')).toBeInTheDocument();
      // SEK 310 rendered as $310 would be a wrong number, not a missing one.
      const sek = inGrid().getByText(/310\.00/);
      expect(sek.textContent).toContain('SEK');
      expect(sek.textContent).not.toContain('$');
    });

    it('formats returns as percent rather than multiplying them again', async () => {
      await mount();
      expect(inGrid().getByText('4.8%')).toBeInTheDocument();
      expect(inGrid().getByText('-1.5%')).toBeInTheDocument();
    });

    it('renders a native cap in its own currency and a missing USD cap as a dash', async () => {
      await mount();
      // A dollar listing shows the same figure in both columns; the Stockholm
      // one shows SEK natively and nothing at all in the dollar column.
      expect(inGrid().getAllByText('$140.0B')).toHaveLength(2);
      expect(inGrid().getByText('SEK 340.0B')).toBeInTheDocument();
      expect(inGrid().queryByText('$340.0B')).toBeNull();
    });

    it('leaves the native cap column unsortable', async () => {
      await mount();
      // Sorting mixed currencies ranks exchange rates, so clicking the header
      // must do nothing at all.
      const header = screen.getByRole('columnheader', { name: /Cap \(native\)/ });
      const before = localStorage.getItem('watchlist-sort-model');
      fireEvent.click(within(header).getByText('Cap (native)'));

      expect(localStorage.getItem('watchlist-sort-model')).toBe(before);
      expect(symbolOrder()).toEqual(['INTC', 'WMT', 'ASSA-B.ST']);
    });

    it('dashes out an unscored symbol rather than showing a zero', async () => {
      await mount();
      const row = inGrid().getByText('ASSA-B.ST').closest('.MuiDataGrid-row') as HTMLElement;
      // Score, USD cap and cache age are all unknown for this row.
      expect(within(row).getAllByText('—').length).toBeGreaterThanOrEqual(3);
      expect(within(row).queryByText('0')).toBeNull();
    });

    it('flags a stale cache with its age', async () => {
      await mount();
      expect(inGrid().getByText('220h')).toBeInTheDocument();
      expect(inGrid().getByText('30h')).toBeInTheDocument();
    });

    it('caps the tag chips and rolls the rest into an overflow chip', async () => {
      await mount();
      // ASSA-B.ST carries 5 tags: 4 chips plus "+1".
      expect(inGrid().getByText('+1')).toBeInTheDocument();
      expect(inGrid().queryByText('extra')).toBeNull();
    });

    it('navigates from the symbol cell', async () => {
      await mount();
      fireEvent.click(inGrid().getByText('INTC'));
      expect(mockNavigate).toHaveBeenCalledWith('/securities/INTC');
    });
  });

  describe('sorting', () => {
    it('opens on composite score descending, as the nightly report ranked', async () => {
      await mount();
      // 72, 61, then the unscored one.
      expect(symbolOrder()).toEqual(['INTC', 'WMT', 'ASSA-B.ST']);
    });

    it('keeps a null out of the way in both sort directions', async () => {
      await mount();
      const capHeader = () => inGrid().getByText('Cap (USD)');

      // Ascending: null is not "smallest".
      fireEvent.click(capHeader());
      await waitFor(() => expect(symbolOrder()[0]).toBe('WMT'));
      expect(symbolOrder()).toEqual(['WMT', 'INTC', 'ASSA-B.ST']);

      // Descending: nor is it "biggest" — DataGrid's own comparator parks it at
      // one end or the other depending on direction.
      fireEvent.click(capHeader());
      await waitFor(() => expect(symbolOrder()[0]).toBe('INTC'));
      expect(symbolOrder()).toEqual(['INTC', 'WMT', 'ASSA-B.ST']);
    });

    it('persists the sort model', async () => {
      await mount();
      fireEvent.click(inGrid().getByText('Cap (USD)'));
      await waitFor(() =>
        expect(JSON.parse(localStorage.getItem('watchlist-sort-model') ?? '[]')).toEqual([
          { field: 'market_cap_usd', sort: 'asc' },
        ]),
      );
    });

    it('restores a persisted sort model on the next mount', async () => {
      localStorage.setItem(
        'watchlist-sort-model',
        JSON.stringify([{ field: 'market_cap_usd', sort: 'asc' }]),
      );
      await mount();
      expect(symbolOrder()).toEqual(['WMT', 'INTC', 'ASSA-B.ST']);
    });

    it('falls back to the default sort when the persisted model is corrupt', async () => {
      localStorage.setItem('watchlist-sort-model', '{{{');
      await mount();
      expect(symbolOrder()).toEqual(['INTC', 'WMT', 'ASSA-B.ST']);
    });

    // Valid JSON of the wrong shape — the `try/catch` never fires, so only a
    // shape check keeps these out.
    it.each([
      ['a bare null', 'null'],
      ['an object', '{"field":"composite_score","sort":"asc"}'],
      ['an empty array', '[]'],
      ['items with no field', '[{"sort":"asc"}]'],
      ['an unknown direction', '[{"field":"composite_score","sort":"sideways"}]'],
    ])('falls back to the default sort when the persisted model is %s', async (_label, stored) => {
      localStorage.setItem('watchlist-sort-model', stored);
      await mount();
      expect(symbolOrder()).toEqual(['INTC', 'WMT', 'ASSA-B.ST']);
    });

    describe('the 30-day tie-break', () => {
      // Same score, different 30d — the only thing separating these rows. The
      // tie-break has to live inside the score comparator: the Community grid
      // truncates a two-item sort model to its first entry, so a second entry
      // would look applied and rank nothing.
      const TIED = [
        watchlistRow('AAA', { composite_score: 61, return_30d: 2 }),
        watchlistRow('BBB', { composite_score: 61, return_30d: 9 }),
        watchlistRow('CCC', { composite_score: 61, return_30d: null }),
      ];

      async function mountTied() {
        mockApi([['/api/watchlist/fundamentals', watchlistFundamentals(TIED)]]);
        renderWithProviders(<WatchlistPage />);
        await waitFor(() => expect(inGrid().getByText('AAA')).toBeInTheDocument());
      }

      it('breaks a score tie on the 30-day return, descending', async () => {
        await mountTied();
        expect(symbolOrder()).toEqual(['BBB', 'AAA', 'CCC']);
      });

      it('follows the column direction into the tie-break', async () => {
        localStorage.setItem(
          'watchlist-sort-model',
          JSON.stringify([{ field: 'composite_score', sort: 'asc' }]),
        );
        await mountTied();
        // 2 before 9 now — but the unknown return stays at the bottom, because
        // nulls are pinned outside the direction.
        expect(symbolOrder()).toEqual(['AAA', 'BBB', 'CCC']);
      });
    });
  });

  describe('filtering', () => {
    it('filters by the search box on symbol and on name', async () => {
      await mount();
      const search = screen.getByLabelText('Search');

      fireEvent.change(search, { target: { value: 'WMT' } });
      await waitFor(() => expect(inGrid().queryByText('INTC')).toBeNull());
      expect(inGrid().getByText('WMT')).toBeInTheDocument();

      // Name match, case-insensitive — "Assa Abloy" is not its ticker.
      fireEvent.change(search, { target: { value: 'assa abloy' } });
      await waitFor(() => expect(inGrid().getByText('ASSA-B.ST')).toBeInTheDocument());
      expect(inGrid().queryByText('WMT')).toBeNull();
    });

    it('toggles a tag on from a grid chip and persists it', async () => {
      await mount();
      fireEvent.click(inGrid().getAllByText('retail')[0]);
      await waitFor(() => expect(inGrid().queryByText('INTC')).toBeNull());
      expect(JSON.parse(localStorage.getItem('watchlist-tag-filter') ?? '[]')).toEqual(['retail']);
    });

    it('toggles the same tag back off', async () => {
      await mount();
      fireEvent.click(inGrid().getAllByText('retail')[0]);
      await waitFor(() => expect(inGrid().queryByText('INTC')).toBeNull());
      fireEvent.click(inGrid().getAllByText('retail')[0]);
      await waitFor(() => expect(inGrid().getByText('INTC')).toBeInTheDocument());
      expect(JSON.parse(localStorage.getItem('watchlist-tag-filter') ?? '[]')).toEqual([]);
    });

    it('restores a persisted tag filter on mount', async () => {
      localStorage.setItem('watchlist-tag-filter', JSON.stringify(['retail']));
      arm();
      renderWithProviders(<WatchlistPage />);
      await waitFor(() => expect(inGrid().getByText('WMT')).toBeInTheDocument());
      expect(inGrid().queryByText('INTC')).toBeNull();
    });

    it('ignores a corrupt persisted filter instead of crashing', async () => {
      localStorage.setItem('watchlist-tag-filter', 'not json');
      await mount();
      expect(inGrid().getByText('WMT')).toBeInTheDocument();
    });

    // These parse cleanly, so the `try/catch` above never sees them: a stored
    // `null` reached `tagFilter.length` and blanked the page.
    it.each([
      ['a bare null', 'null'],
      ['an object', '{}'],
      ['a string', '"retail"'],
      ['a mixed array', '["retail",7]'],
    ])('ignores a persisted filter that is %s', async (_label, stored) => {
      localStorage.setItem('watchlist-tag-filter', stored);
      await mount();
      expect(screen.getByText(/3 of 3 securities/)).toBeInTheDocument();
      expect(inGrid().getByText('WMT')).toBeInTheDocument();
    });

    it('clears every tag from the toolbar button and counts what is shown', async () => {
      localStorage.setItem('watchlist-tag-filter', JSON.stringify(['retail']));
      arm();
      renderWithProviders(<WatchlistPage />);
      await waitFor(() => expect(inGrid().getByText('WMT')).toBeInTheDocument());
      expect(screen.getByText(/1 of 3 securities/)).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: /Clear Tags \(1\)/i }));
      await waitFor(() => expect(inGrid().getByText('INTC')).toBeInTheDocument());
      expect(screen.getByText(/3 of 3 securities/)).toBeInTheDocument();
      expect(JSON.parse(localStorage.getItem('watchlist-tag-filter') ?? 'null')).toEqual([]);
    });
  });

  describe('tag editor', () => {
    it('PATCHes the whole chip set, including a newly added chip', async () => {
      const api = await mount([['/api/watchlist/INTC', { symbol: 'INTC', tags: ['chips', 'ai'] }]]);
      fireEvent.click(screen.getByRole('button', { name: 'Edit tags for INTC' }));

      const input = await screen.findByLabelText('Add tag');
      fireEvent.change(input, { target: { value: 'ai' } });
      fireEvent.keyDown(input, { key: 'Enter' });
      fireEvent.click(screen.getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
      const patch = api.calls.find(([, init]) => init?.method === 'PATCH')!;
      expect(patch[0]).toContain('/api/watchlist/INTC');
      expect(JSON.parse(String(patch[1]?.body))).toEqual({ tags: ['chips', 'ai'] });
    });

    it('sends an empty set when the last chip is deleted', async () => {
      // Only expressible because the server replaces rather than merges — a
      // delta payload could never clear a tag.
      const api = await mount([['/api/watchlist/INTC', { symbol: 'INTC', tags: [] }]]);
      fireEvent.click(screen.getByRole('button', { name: 'Edit tags for INTC' }));

      const dialog = within(await screen.findByRole('dialog'));
      fireEvent.click(dialog.getByTestId('CancelIcon'));
      expect(
        await screen.findByText(/saving now clears every tag on this symbol/i),
      ).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: 'Save' }));
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
      const patch = api.calls.find(([, init]) => init?.method === 'PATCH')!;
      expect(JSON.parse(String(patch[1]?.body))).toEqual({ tags: [] });
    });

    it('keeps the dialog open and shows why when the save fails', async () => {
      await mount([['/api/watchlist/INTC', { __status: 500, error: 'tags rejected' }]]);
      fireEvent.click(screen.getByRole('button', { name: 'Edit tags for INTC' }));
      fireEvent.click(await screen.findByRole('button', { name: 'Save' }));

      await waitFor(() => expect(screen.getByText(/tags rejected/i)).toBeInTheDocument());
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });
  });

  describe('removal', () => {
    it('warns that the list is shared before deleting', async () => {
      await mount();
      fireEvent.click(screen.getByRole('button', { name: 'Remove WMT from watchlist' }));
      expect(
        await screen.findByText(/watchlist is shared, so\s+this removes it for everyone/i),
      ).toBeInTheDocument();
    });

    it('DELETEs on confirm and closes', async () => {
      const api = await mount([['/api/watchlist/WMT', { symbol: 'WMT', removed: true }]]);
      fireEvent.click(screen.getByRole('button', { name: 'Remove WMT from watchlist' }));
      fireEvent.click(await screen.findByRole('button', { name: 'Remove' }));

      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
      const del = api.calls.find(([, init]) => init?.method === 'DELETE')!;
      expect(del[0]).toContain('/api/watchlist/WMT');
    });

    it('cancelling deletes nothing', async () => {
      const api = await mount();
      fireEvent.click(screen.getByRole('button', { name: 'Remove WMT from watchlist' }));
      fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }));

      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
      expect(api.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false);
    });

    it('shows the failure rather than pretending the row is gone', async () => {
      await mount([['/api/watchlist/WMT', { __status: 404, error: 'WMT not found in watchlist' }]]);
      fireEvent.click(screen.getByRole('button', { name: 'Remove WMT from watchlist' }));
      fireEvent.click(await screen.findByRole('button', { name: 'Remove' }));

      await waitFor(() => expect(screen.getByText(/not found in watchlist/i)).toBeInTheDocument());
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });
  });

  it('opens the shared Add Security dialog', async () => {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: /Add Security/i }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });
});
