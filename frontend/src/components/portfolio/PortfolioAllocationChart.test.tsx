import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import PortfolioAllocationChart from './PortfolioAllocationChart';
import { symbolRow } from '../../testUtils';
import type { SymbolRow } from '../../api/portfolioTypes';

const rows = (...overrides: object[]) =>
  overrides.map((o) => symbolRow(o) as unknown as SymbolRow);

describe('PortfolioAllocationChart', () => {
  it('stacks the gain above the cost basis for a winner', () => {
    const { container } = render(
      <PortfolioAllocationChart rows={rows({ symbol: 'INTC', bar_base: 300, bar_gain: 50, bar_loss: 0 })} />,
    );

    const base = container.querySelector('[data-testid="alloc-base-INTC"]')!;
    const gain = container.querySelector('[data-testid="alloc-gain-INTC"]')!;
    expect(base).toBeInTheDocument();
    expect(gain).toBeInTheDocument();
    expect(container.querySelector('[data-testid="alloc-loss-INTC"]')).toBeNull();

    // Gain sits directly on top of the basis: its bottom edge is the basis' top.
    const gainBottom = Number(gain.getAttribute('y')) + Number(gain.getAttribute('height'));
    expect(gainBottom).toBeCloseTo(Number(base.getAttribute('y')), 5);

    expect(screen.getByText('$300.00')).toBeInTheDocument();
    expect(screen.getByText('$50.00')).toBeInTheDocument();
  });

  it('cuts the loss down into the cost basis for a loser', () => {
    const { container } = render(
      <PortfolioAllocationChart
        rows={rows({ symbol: 'WDC', bar_base: 300, bar_gain: 0, bar_loss: -40 })}
      />,
    );

    const base = container.querySelector('[data-testid="alloc-base-WDC"]')!;
    const loss = container.querySelector('[data-testid="alloc-loss-WDC"]')!;
    expect(container.querySelector('[data-testid="alloc-gain-WDC"]')).toBeNull();

    // The red block overlays the TOP of the orange one, so the visible top of
    // the bar is current value — the legacy report's semantics exactly.
    expect(Number(loss.getAttribute('y'))).toBeCloseTo(Number(base.getAttribute('y')), 5);
    expect(Number(loss.getAttribute('height'))).toBeGreaterThan(0);
    expect(screen.getByText('-$40.00')).toBeInTheDocument();
  });

  it('draws neither coloured segment at break-even', () => {
    const { container } = render(
      <PortfolioAllocationChart
        rows={rows({ symbol: 'AAPL', bar_base: 300, bar_gain: 0, bar_loss: 0 })}
      />,
    );
    expect(container.querySelector('[data-testid="alloc-base-AAPL"]')).toBeInTheDocument();
    expect(container.querySelector('[data-testid="alloc-gain-AAPL"]')).toBeNull();
    expect(container.querySelector('[data-testid="alloc-loss-AAPL"]')).toBeNull();
  });

  it('drops an unpriced symbol instead of drawing a zero-height bar', () => {
    const { container } = render(
      <PortfolioAllocationChart
        rows={rows(
          { symbol: 'INTC', bar_base: 300, bar_gain: 50, bar_loss: 0 },
          { symbol: 'ZZZZ', bar_base: null, bar_gain: null, bar_loss: null },
        )}
      />,
    );
    expect(container.querySelector('[data-testid="alloc-base-INTC"]')).toBeInTheDocument();
    expect(container.querySelector('[data-testid="alloc-base-ZZZZ"]')).toBeNull();
  });

  it('orders the biggest position first', () => {
    const { container } = render(
      <PortfolioAllocationChart
        rows={rows(
          { symbol: 'SMALL', bar_base: 100, bar_gain: 0, bar_loss: 0 },
          { symbol: 'BIG', bar_base: 300, bar_gain: 50, bar_loss: 0 },
        )}
      />,
    );
    const big = Number(
      container.querySelector('[data-testid="alloc-base-BIG"]')!.getAttribute('x'),
    );
    const small = Number(
      container.querySelector('[data-testid="alloc-base-SMALL"]')!.getAttribute('x'),
    );
    expect(big).toBeLessThan(small);
  });

  it('renders nothing at all with no chartable rows', () => {
    const { container } = render(<PortfolioAllocationChart rows={[]} />);
    expect(container.querySelector('svg')).toBeNull();
    expect(screen.queryByTestId('portfolio-allocation-chart')).not.toBeInTheDocument();
  });
});
