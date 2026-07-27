import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';

import DiscoveryScatter from './DiscoveryScatter';
import { renderWithProviders } from '../../testUtils';
import type { TestedPair } from '../../api/arbitrage';

const CRITICALS = { '0.01': -3.9, '0.05': -3.34, '0.1': -3.04 };

function pair(overrides: Partial<TestedPair> = {}): TestedPair {
  return {
    security: 'NEM',
    underlying: 'GC=F',
    economic_link: true,
    correlation: 0.733,
    statistic: -2.87,
    reject_at: null,
    half_life_days: 6.4,
    passed: false,
    failed_because: 'not cointegrated',
    ...overrides,
  };
}

describe('DiscoveryScatter', () => {
  it('summarises how many pairs passed', () => {
    renderWithProviders(
      <DiscoveryScatter
        tested={[pair(), pair({ security: 'AEM', statistic: -4.2, passed: true, failed_because: null })]}
        criticalValues={CRITICALS}
      />,
    );
    expect(screen.getByText(/1 of 2 pairs passed/)).toBeInTheDocument();
    expect(screen.getByText(/filled = passed, hollow = rejected/)).toBeInTheDocument();
  });

  it('draws rejected points hollow and passed points filled', () => {
    const { container } = renderWithProviders(
      <DiscoveryScatter
        tested={[pair(), pair({ security: 'AEM', statistic: -4.2, passed: true, failed_because: null })]}
        criticalValues={CRITICALS}
      />,
    );
    const circles = Array.from(
      container.querySelectorAll('[data-testid="discovery-scatter"] circle'),
    );
    expect(circles).toHaveLength(2);
    const fills = circles.map((c) => c.getAttribute('fill'));
    expect(fills).toContain('none'); // the rejected near-miss
    expect(fills.filter((f) => f !== 'none')).toHaveLength(1);
  });

  it('renders a critical-value line per level', () => {
    const { container } = renderWithProviders(
      <DiscoveryScatter tested={[pair()]} criticalValues={CRITICALS} />,
    );
    const svg = container.querySelector('[data-testid="discovery-scatter"]')!;
    // Scoped by class — d3 axis ticks are <line> elements too.
    expect(svg.querySelectorAll('line.critical-line')).toHaveLength(3);
    expect(svg.textContent).toContain('5%');
  });

  it('explains a rejected point in its tooltip', () => {
    const { container } = renderWithProviders(
      <DiscoveryScatter tested={[pair()]} criticalValues={CRITICALS} />,
    );
    const title = container.querySelector('[data-testid="discovery-scatter"] title')!;
    expect(title.textContent).toContain('NEM ~ GC=F');
    expect(title.textContent).toContain('-2.87');
    expect(title.textContent).toContain('rejected: not cointegrated');
  });

  it('skips pairs with no measurable statistic', () => {
    const { container } = renderWithProviders(
      <DiscoveryScatter
        tested={[pair(), pair({ security: 'GHOST', statistic: null, correlation: null })]}
        criticalValues={CRITICALS}
      />,
    );
    expect(
      container.querySelectorAll('[data-testid="discovery-scatter"] circle'),
    ).toHaveLength(1);
    expect(screen.getByText(/0 of 1 pairs passed/)).toBeInTheDocument();
  });

  it('degrades when nothing was testable', () => {
    renderWithProviders(<DiscoveryScatter tested={[]} criticalValues={CRITICALS} />);
    expect(screen.getByText(/Nothing testable in this sweep/)).toBeInTheDocument();
  });
});
