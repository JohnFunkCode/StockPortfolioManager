import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import PortfolioPage from './PortfolioPage';
import { renderWithProviders } from '../../testUtils';

describe('PortfolioPage', () => {
  it('renders the Portfolio heading', () => {
    renderWithProviders(<PortfolioPage />);
    expect(screen.getByText('Portfolio')).toBeInTheDocument();
  });
});
