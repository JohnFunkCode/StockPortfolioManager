import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import PortfolioCheckPage, { PORTFOLIO_CHECK_SRC } from './PortfolioCheckPage';

describe('PortfolioCheckPage', () => {
  it('renders the heading', () => {
    render(<PortfolioCheckPage />);
    expect(screen.getByRole('heading', { name: 'Portfolio Check' })).toBeInTheDocument();
  });

  it('says plainly that the report is a made-up sample, not the viewer\'s portfolio', () => {
    render(<PortfolioCheckPage />);
    expect(screen.getByRole('alert')).toHaveTextContent(/Sample portfolio/);
    expect(screen.getByRole('alert')).toHaveTextContent(/not your portfolio/);
  });

  it('frames the pre-built sample report in the sandbox', () => {
    render(<PortfolioCheckPage />);
    const frame = screen.getByTitle('Portfolio Check sample report');
    expect(frame.tagName).toBe('IFRAME');
    expect(frame).toHaveAttribute('src', PORTFOLIO_CHECK_SRC);
    expect(PORTFOLIO_CHECK_SRC).toBe('/research/portfolio-check-sample.html');
    expect(frame.getAttribute('sandbox')).toBe('allow-scripts');
  });
});
