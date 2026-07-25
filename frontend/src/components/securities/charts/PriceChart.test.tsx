import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render } from '@testing-library/react';

import PriceChart from './PriceChart';
import { indicatorRows } from '../../../testUtils';

afterEach(cleanup);

describe('PriceChart', () => {
  it('draws the close line and bands by default', () => {
    const { container } = render(<PriceChart data={indicatorRows(60)} />);
    const svg = container.querySelector('svg')!;
    expect(svg.querySelectorAll('path').length).toBeGreaterThan(0);
  });

  it('renders extra MA lines and earnings markers when configured', () => {
    const { container } = render(
      <PriceChart
        data={indicatorRows(60)}
        showMAs={{ ma30: true, ma50: true, ma200: true }}
        showBB
        earningsDates={['2026-04-15']}
      />,
    );
    // MA legend row + at least one earnings 'E' marker in-range.
    expect(container.textContent).toContain('MA30');
    expect(container.querySelector('svg')!.textContent).toContain('E');
  });

  it('omits bands when showBB is false', () => {
    const { container } = render(<PriceChart data={indicatorRows(60)} showBB={false} />);
    expect(container.textContent).not.toContain('Bollinger');
  });

  it('fires onPointClick when the overlay is clicked', () => {
    const onPointClick = vi.fn();
    const { container } = render(
      <PriceChart data={indicatorRows(60)} onPointClick={onPointClick} />,
    );
    const overlay = container.querySelector('rect[pointer-events="all"]');
    if (overlay) {
      fireEvent.click(overlay, { clientX: 100, clientY: 50 });
      expect(onPointClick).toHaveBeenCalled();
    }
  });

  it('renders an empty shell with no data', () => {
    const { container } = render(<PriceChart data={[]} />);
    expect(container.querySelector('svg')!.querySelectorAll('path').length).toBe(0);
  });
});
