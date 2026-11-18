import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';

import BookSummaryStrip from './BookSummaryStrip';
import { bookPerformance, portfolioTotals, realizedResponse } from '../../testUtils';
import type { BookPerformance } from '../../api/books';

const performance = (overrides: object = {}) => bookPerformance(overrides) as BookPerformance;

const realizedTotals = (overrides: object = {}) => ({ ...realizedResponse().totals, ...overrides });

describe('BookSummaryStrip', () => {
  it('shows book value, unrealized and realized for the last year', () => {
    render(
      <BookSummaryStrip
        holdings={portfolioTotals()}
        realizedYear={realizedTotals({ realized_pnl: 80 })}
      />,
    );
    const strip = within(screen.getByTestId('book-summary-strip'));
    expect(strip.getByText('Book value')).toBeInTheDocument();
    expect(strip.getByText('$350.00')).toBeInTheDocument();
    expect(strip.getByText('Unrealized')).toBeInTheDocument();
    expect(strip.getByText('$50.00', { exact: false })).toBeInTheDocument();
    expect(strip.getByText('16.67%')).toBeInTheDocument();
    expect(strip.getByText('Realized, last year')).toBeInTheDocument();
    expect(strip.getByText('$80.00')).toBeInTheDocument();
  });

  it('links to each section of the page', () => {
    render(<BookSummaryStrip />);
    const nav = within(screen.getByRole('navigation', { name: 'Portfolio sections' }));
    expect(nav.getByRole('link', { name: 'Holdings' })).toHaveAttribute('href', '#holdings');
    expect(nav.getByRole('link', { name: 'Realized' })).toHaveAttribute('href', '#realized');
    expect(nav.getByRole('link', { name: 'Performance' })).toHaveAttribute('href', '#performance');
    expect(nav.getByRole('link', { name: 'Cash and orders' })).toHaveAttribute('href', '#cash');
  });

  it('shows cash beside the holdings, and renames them so the two are not confused', () => {
    render(<BookSummaryStrip holdings={portfolioTotals()} cash={1250.5} />);
    const strip = within(screen.getByTestId('book-summary-strip'));
    expect(strip.getByText('Holdings value')).toBeInTheDocument();
    expect(strip.getByText('$350.00')).toBeInTheDocument();
    expect(strip.getByText('Cash')).toBeInTheDocument();
    expect(strip.getByText('$1,250.50')).toBeInTheDocument();
    expect(strip.queryByText('Book value')).toBeNull();
    // No sum of the two is made here.
    expect(strip.queryByText('$1,600.50')).toBeNull();
  });

  it('shows a zero or negative cash balance as a figure, and no cash it was not given', () => {
    const { rerender } = render(<BookSummaryStrip cash={0} />);
    expect(screen.getByText('$0.00')).toBeInTheDocument();
    rerender(<BookSummaryStrip cash={-12.34} />);
    expect(screen.getByText('-$12.34')).toBeInTheDocument();
    rerender(<BookSummaryStrip holdings={portfolioTotals()} cash={null} />);
    expect(screen.queryByText('Cash')).toBeNull();
    expect(screen.getByText('Book value')).toBeInTheDocument();
  });

  it('leaves out a figure it does not have, with no zero or dash in its place', () => {
    render(<BookSummaryStrip holdings={null} realizedYear={undefined} />);
    const strip = within(screen.getByTestId('book-summary-strip'));
    expect(strip.queryByText('Book value')).toBeNull();
    expect(strip.queryByText('Unrealized')).toBeNull();
    expect(strip.queryByText('Realized, last year')).toBeNull();
    expect(strip.queryByText(/\$|N\/A|—/)).toBeNull();
    // Return vs SPY is not built yet (plan step 5.3), so it is not shown at all.
    expect(strip.queryByText(/SPY/)).toBeNull();
  });

  it('leaves realized out when no sale could be summed', () => {
    render(
      <BookSummaryStrip
        holdings={portfolioTotals()}
        realizedYear={realizedTotals({ realized_pnl: null })}
      />,
    );
    expect(screen.queryByText('Realized, last year')).toBeNull();
    expect(screen.getByText('Book value')).toBeInTheDocument();
  });

  it('shows a realized loss as a negative figure', () => {
    render(<BookSummaryStrip realizedYear={realizedTotals({ realized_pnl: -12.5 })} />);
    expect(screen.getByText('-$12.50')).toBeInTheDocument();
  });

  it('shows unrealized without a percentage when there is none', () => {
    render(<BookSummaryStrip holdings={portfolioTotals({ total_gain_loss_pct: null })} />);
    expect(screen.getByText('Unrealized')).toBeInTheDocument();
    expect(screen.queryByText(/%/)).toBeNull();
  });

  it("shows the year's return against SPY and the newest snapshot's book value (5.3)", () => {
    render(<BookSummaryStrip performanceYear={performance()} />);
    const strip = within(screen.getByTestId('book-summary-strip'));
    expect(strip.getByText('Return, last year')).toBeInTheDocument();
    expect(strip.getByText('+11.00% vs SPY')).toBeInTheDocument();
    expect(strip.getByText('Book value, Nov 4, 2026 close')).toBeInTheDocument();
    expect(strip.getByText('$1,210.00')).toBeInTheDocument();
  });

  it('says a return measured over negative cash is unreliable (decision 8)', () => {
    render(<BookSummaryStrip performanceYear={performance({ cash_negative_count: 2 })} />);
    expect(screen.getByText('Return, last year (unreliable)')).toBeInTheDocument();
  });

  it('leaves the return out when there is none yet, and says nothing about SPY without it', () => {
    render(
      <BookSummaryStrip
        performanceYear={performance({ twr: null, latest: null, vs_benchmark: null })}
      />,
    );
    expect(screen.queryByText(/Return, last year/)).toBeNull();
    expect(screen.queryByText(/Book value,/)).toBeNull();
    render(<BookSummaryStrip performanceYear={performance({ vs_benchmark: null })} />);
    expect(screen.queryByText(/vs SPY/)).toBeNull();
    expect(screen.getByText('Return, last year')).toBeInTheDocument();
  });
});
