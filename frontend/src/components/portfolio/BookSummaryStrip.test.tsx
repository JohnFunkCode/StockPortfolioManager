import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';

import BookSummaryStrip from './BookSummaryStrip';
import { portfolioTotals, realizedResponse } from '../../testUtils';

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
});
