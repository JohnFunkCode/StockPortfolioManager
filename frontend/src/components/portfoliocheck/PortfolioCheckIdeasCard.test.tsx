import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import PortfolioCheckIdeasCard from './PortfolioCheckIdeasCard';
import { checkReport } from './portfolioCheckFixtures';
import { mockApi, renderWithProviders } from '../../testUtils';

afterEach(() => {
  vi.unstubAllGlobals();
});

const ROUTE = '/api/portfolio/check';

describe('PortfolioCheckIdeasCard', () => {
  it('shows a loading state first', () => {
    mockApi([[ROUTE, checkReport()]]);
    renderWithProviders(<PortfolioCheckIdeasCard />);
    expect(screen.getByText(/Loading industry ideas/)).toBeInTheDocument();
  });

  it('lists the ideas with their largest members', async () => {
    mockApi([[ROUTE, checkReport()]]);
    renderWithProviders(<PortfolioCheckIdeasCard />);
    const idea = within(await screen.findByTestId('idea-Gold'));
    expect(idea.getByText('Precious Metals')).toBeInTheDocument();
    expect(idea.getByText(/rank 1 · \+85% vs market/)).toBeInTheDocument();
    expect(idea.getByText('NEM (Newmont; +10% vs peers), XYZ (Xyz Co)')).toBeInTheDocument();
  });

  it('shows the industry ranks on request', async () => {
    mockApi([[ROUTE, checkReport()]]);
    renderWithProviders(<PortfolioCheckIdeasCard />);
    const toggle = await screen.findByRole('button', { name: /Show all 3 industry ranks/ });
    expect(screen.queryByRole('table', { name: 'Industry ranks' })).not.toBeInTheDocument();
    await userEvent.click(toggle);
    const table = within(await screen.findByRole('table', { name: 'Industry ranks' }));
    expect(table.getByText('Beer & Liquor')).toBeInTheDocument();
    expect(table.getByText('−18%')).toBeInTheDocument();
    expect(table.getByText('—')).toBeInTheDocument();          // Oil's missing 5-year wind
    expect(screen.getByRole('button', { name: /Hide all 3 industry ranks/ })).toBeInTheDocument();
  });

  it('says so when every Buy-zone industry is already held', async () => {
    mockApi([[ROUTE, checkReport({ ideas: [] })]]);
    renderWithProviders(<PortfolioCheckIdeasCard />);
    await waitFor(() => expect(screen.getByText(/already hold every industry/)).toBeInTheDocument());
  });

  it('shows an error when the check fails', async () => {
    mockApi([[ROUTE, () => ({ __status: 500, error: 'boom' })]]);
    renderWithProviders(<PortfolioCheckIdeasCard />);
    await waitFor(() => expect(screen.getByText(/Couldn't load the industry ideas/)).toBeInTheDocument());
  });
});
