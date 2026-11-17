import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import PortfolioCheckCard, { actionColor, signedPercent } from './PortfolioCheckCard';
import { checkHolding, checkReport } from './portfolioCheckFixtures';
import { mockApi, renderWithProviders } from '../../testUtils';

afterEach(() => {
  vi.unstubAllGlobals();
});

const ROUTE = '/api/portfolio/check';

describe('PortfolioCheckCard', () => {
  it('shows a loading state first', () => {
    mockApi([[ROUTE, checkReport()]]);
    renderWithProviders(<PortfolioCheckCard />);
    expect(screen.getByText(/Checking your portfolio/)).toBeInTheDocument();
  });

  it('renders each holding with its action and the server counts', async () => {
    mockApi([[ROUTE, checkReport()]]);
    renderWithProviders(<PortfolioCheckCard />);
    await waitFor(() => expect(screen.getByTestId('portfolio-check-card')).toBeInTheDocument());
    const ko = within(screen.getByTestId('check-row-KO'));
    expect(ko.getByText('Sell')).toBeInTheDocument();
    expect(ko.getByText('Coca Cola Co')).toBeInTheDocument();
    expect(ko.getByText('rank 47 of 3')).toBeInTheDocument();
    expect(ko.getByText('4.3%')).toBeInTheDocument();
    expect(ko.getByText('+12%')).toBeInTheDocument();
    expect(ko.getByText('$6,800.00')).toBeInTheDocument();
    expect(within(screen.getByTestId('check-row-XOM')).getByText('−5%')).toBeInTheDocument();
    expect(screen.getByText('1 Sell')).toBeInTheDocument();
    expect(screen.getByText('1 Buy / add')).toBeInTheDocument();
    expect(screen.getByText(/\$157,930\.00/)).toBeInTheDocument();
  });

  it('reveals the reasons and tax note when a row is clicked', async () => {
    mockApi([[ROUTE, checkReport()]]);
    renderWithProviders(<PortfolioCheckCard />);
    await waitFor(() => expect(screen.getByTestId('check-row-KO')).toBeInTheDocument());
    expect(screen.queryByTestId('check-details-KO')).not.toBeInTheDocument();
    await userEvent.click(screen.getByTestId('check-row-KO'));
    const details = within(await screen.findByTestId('check-details-KO'));
    expect(details.getByText(/ranks 47 of 49 industries/)).toBeInTheDocument();
    expect(details.getByText(/estimated tax \$413/)).toBeInTheDocument();
    expect(screen.getByTestId('check-row-KO')).toHaveAttribute('aria-expanded', 'true');
  });

  it('warns about holdings without a current price', async () => {
    mockApi([[ROUTE, checkReport({ unpriced: ['ZZZ'] })]]);
    renderWithProviders(<PortfolioCheckCard />);
    await waitFor(() => expect(screen.getByText(/No current price for ZZZ/)).toBeInTheDocument());
  });

  it('explains an empty portfolio', async () => {
    mockApi([[ROUTE, checkReport({ holdings: [], action_counts: [] })]]);
    renderWithProviders(<PortfolioCheckCard />);
    await waitFor(() => expect(screen.getByText(/No open lots yet/)).toBeInTheDocument());
  });

  it('shows an error when the check fails', async () => {
    mockApi([[ROUTE, () => ({ __status: 500, error: 'boom' })]]);
    renderWithProviders(<PortfolioCheckCard />);
    await waitFor(() => expect(screen.getByText(/Couldn't run the Portfolio Check/)).toBeInTheDocument());
  });

  it('renders a Review holding with no industry rank', async () => {
    const review = checkHolding({ ticker: 'CRSP', action: 'Review', industry: 'Unclassified', industry_rank: null,
                                  industry_code: null, vs_peers_12m: null, tax_note: '' });
    mockApi([[ROUTE, checkReport({ holdings: [review], action_counts: [{ action: 'Review', count: 1 }] })]]);
    renderWithProviders(<PortfolioCheckCard />);
    const row = within(await screen.findByTestId('check-row-CRSP'));
    expect(row.getByText('Unclassified')).toBeInTheDocument();
    expect(row.getByText('—')).toBeInTheDocument();
    expect(row.queryByText(/rank/)).not.toBeInTheDocument();
  });
});

describe('formatting helpers', () => {
  it('colours actions', () => {
    expect(actionColor('Sell')).toBe('error');
    expect(actionColor('Sell after Dec 02, 2026')).toBe('error');
    expect(actionColor('Hold, on watch')).toBe('warning');
    expect(actionColor('Review')).toBe('warning');
    expect(actionColor('Buy / add')).toBe('success');
    expect(actionColor('Hold (at size limit)')).toBe('info');
    expect(actionColor('Hold')).toBe('default');
  });

  it('signs percents', () => {
    expect(signedPercent(0.123)).toBe('+12%');
    expect(signedPercent(-0.05)).toBe('−5%');
    expect(signedPercent(0.001)).toBe('0%');
    expect(signedPercent(null)).toBe('—');
  });
});
