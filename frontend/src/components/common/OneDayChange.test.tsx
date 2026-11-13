/**
 * The shared 1d cell (issue #518). The numbers are the server's; these tests
 * pin what the cell adds: the stale marker, the per-session badge, and the
 * stale-last ordering.
 */
import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import OneDayChange, { compareOneDay, sessionBadge } from './OneDayChange';

describe('OneDayChange', () => {
  it('renders the display value as percent', () => {
    render(<OneDayChange row={{ return_1d_display: 1.234, return_1d_stale: false }} />);
    expect(screen.getByText('1.23%')).toBeInTheDocument();
    expect(screen.queryByLabelText('stale')).not.toBeInTheDocument();
    expect(screen.queryByTestId('session-badge')).not.toBeInTheDocument();
  });

  it('honours the decimals prop', () => {
    render(<OneDayChange row={{ return_1d_display: -1.26 }} decimals={1} />);
    expect(screen.getByText('-1.3%')).toBeInTheDocument();
  });

  it('dashes out a row with no figure rather than showing zero', () => {
    render(<OneDayChange row={{ return_1d_display: null, return_1d_stale: true }} />);
    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.queryByLabelText('stale')).not.toBeInTheDocument();
  });

  it('marks a stale figure', () => {
    render(<OneDayChange row={{ return_1d_display: 0.4, return_1d_stale: true }} />);
    expect(screen.getByText('0.40%')).toBeInTheDocument();
    expect(screen.getByLabelText('stale')).toBeInTheDocument();
  });

  it('says a stale figure has no live quote', async () => {
    render(<OneDayChange row={{ return_1d_display: 0.4, return_1d_stale: true }} />);
    fireEvent.mouseOver(screen.getByLabelText('stale'));
    expect(await screen.findByText(/No live quote/)).toBeInTheDocument();
  });

  it('says an earlier-session figure is the previous session\'s change', async () => {
    render(
      <OneDayChange
        row={{ return_1d_display: -2.5, return_1d_stale: true, quote_earlier_session: true }}
      />,
    );
    expect(screen.getByText('-2.50%')).toBeInTheDocument();
    fireEvent.mouseOver(screen.getByLabelText('stale'));
    expect(await screen.findByText(/No trade in the current session yet/)).toBeInTheDocument();
    expect(screen.queryByText(/No live quote/)).not.toBeInTheDocument();
  });

  it('shows the after-hours change beside 1d after the close', () => {
    render(
      <OneDayChange
        row={{ return_1d_display: 1.5, market_state: 'POST', post_change_pct: -0.25, pre_change_pct: 9 }}
      />,
    );
    expect(screen.getByTestId('session-badge')).toHaveTextContent('AH -0.25%');
  });

  it('shows the pre-market change before the open, signed', () => {
    render(
      <OneDayChange
        row={{ return_1d_display: 1.5, market_state: 'PRE', pre_change_pct: 0.4, post_change_pct: 9 }}
      />,
    );
    expect(screen.getByTestId('session-badge')).toHaveTextContent('Pre +0.40%');
  });
});

describe('sessionBadge', () => {
  it.each([
    ['PRE', 'Pre'],
    ['PREPRE', 'Pre'],
    ['pre', 'Pre'],
    ['POST', 'AH'],
    ['POSTPOST', 'AH'],
    ['CLOSED', 'AH'],
  ])('%s shows the %s change', (state, label) => {
    expect(sessionBadge(state, 0.5, -0.5)?.label).toBe(label);
  });

  it('shows nothing during the regular session', () => {
    expect(sessionBadge('REGULAR', 0.5, -0.5)).toBeNull();
  });

  it('shows nothing when the state is unknown or missing', () => {
    expect(sessionBadge(null, 0.5, -0.5)).toBeNull();
    expect(sessionBadge(undefined, 0.5, -0.5)).toBeNull();
    expect(sessionBadge('HALTED', 0.5, -0.5)).toBeNull();
  });

  it('shows nothing when that session has no change yet', () => {
    expect(sessionBadge('PRE', null, -0.5)).toBeNull();
    expect(sessionBadge('POST', 0.5, null)).toBeNull();
  });

  it('keeps a zero change', () => {
    expect(sessionBadge('POST', null, 0)?.value).toBe(0);
  });
});

describe('compareOneDay', () => {
  const live = (v: number) => ({ return_1d_display: v, return_1d_stale: false });
  const stale = (v: number) => ({ return_1d_display: v, return_1d_stale: true });
  const none = { return_1d_display: null, return_1d_stale: true };
  const sortBy = (direction: 'asc' | 'desc') =>
    [stale(9), live(1), none, live(-2), stale(-9), live(3)]
      .sort((a, b) => compareOneDay(a, b, direction))
      .map((r) => `${r.return_1d_display}${r.return_1d_stale ? 's' : ''}`);

  it('ascending: live rows low to high, then stale, then empty', () => {
    expect(sortBy('asc')).toEqual(['-2', '1', '3', '-9s', '9s', 'nulls']);
  });

  it('descending: live rows high to low, stale and empty still last', () => {
    expect(sortBy('desc')).toEqual(['3', '1', '-2', '9s', '-9s', 'nulls']);
  });

  it('treats two empty rows as equal', () => {
    expect(compareOneDay(none, none, 'asc')).toBe(0);
  });
});
