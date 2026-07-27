import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';

import EmptyPortfolio from './EmptyPortfolio';
import { renderWithProviders } from '../../testUtils';

describe('EmptyPortfolio', () => {
  it('renders friendly copy inviting the user to add a first position', () => {
    renderWithProviders(<EmptyPortfolio />);
    expect(screen.getByText('Your portfolio is empty')).toBeInTheDocument();
    expect(screen.getByText(/add your first position/i)).toBeInTheDocument();
  });

  it('does not display any identifying information', () => {
    renderWithProviders(<EmptyPortfolio />);
    expect(screen.queryByText(/@/)).not.toBeInTheDocument();
    expect(screen.queryByText(/contact/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/email/i)).not.toBeInTheDocument();
  });

  it('does not use the restricted-access copy', () => {
    renderWithProviders(<EmptyPortfolio />);
    expect(screen.queryByText(/restricted to authorized users/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/prosecuted/i)).not.toBeInTheDocument();
  });
});
