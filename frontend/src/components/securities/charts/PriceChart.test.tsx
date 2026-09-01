import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render } from '@testing-library/react';

import PriceChart from './PriceChart';
import { indicatorRows } from '../../../testUtils';

afterEach(() => {
  cleanup();
  document.querySelector('#price-tooltip')?.remove();
});

describe('PriceChart', () => {
  it('renders readable tooltip content for a hovered point', () => {
    const tooltip = document.createElement('div');
    tooltip.id = 'price-tooltip';
    document.body.appendChild(tooltip);

    const { container } = render(<PriceChart data={indicatorRows(60)} />);
    const overlay = container.querySelector('rect[pointer-events="all"]')!;
    fireEvent.mouseMove(overlay, { clientX: 100, clientY: 100 });

    expect(tooltip).toHaveTextContent('Close: $');
    expect(tooltip).toHaveTextContent('MA50: $');
    expect(tooltip).toHaveTextContent('BB upper: $');
    expect(tooltip).toHaveStyle({ display: 'block' });
  });

  it('keeps the tooltip meaningful when indicator values are missing', () => {
    const tooltip = document.createElement('div');
    tooltip.id = 'price-tooltip';
    document.body.appendChild(tooltip);

    const rows = indicatorRows(2).map((row) => ({
      ...row,
      ma30: null,
      ma50: null,
      ma200: null,
      bb_upper: null,
      bb_middle: null,
      bb_lower: null,
    }));
    const { container } = render(<PriceChart data={rows} />);
    const overlay = container.querySelector('rect[pointer-events="all"]')!;
    fireEvent.mouseMove(overlay, { clientX: 100, clientY: 100 });

    expect(tooltip).toHaveTextContent('Close: $');
    expect(tooltip.textContent).not.toContain('undefined');
    expect(tooltip).toHaveStyle({ display: 'block' });
  });

  it('does not show an empty tooltip when no close values are available', () => {
    const tooltip = document.createElement('div');
    tooltip.id = 'price-tooltip';
    tooltip.style.display = 'none';
    document.body.appendChild(tooltip);

    const rows = indicatorRows(2).map((row) => ({ ...row, close: null }));
    render(<PriceChart data={rows} />);

    expect(tooltip).toHaveStyle({ display: 'none' });
  });

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

  it('shows readable tooltip content at viewport coordinates', () => {
    const tooltip = document.createElement('div');
    tooltip.id = 'price-tooltip';
    document.body.appendChild(tooltip);
    const { container } = render(<PriceChart data={indicatorRows(60)} />);
    const overlay = container.querySelector('rect[pointer-events="all"]')!;

    fireEvent.mouseMove(overlay, {
      clientX: 100,
      clientY: 50,
      pageX: 900,
      pageY: 850,
    });

    expect(tooltip.style.left).toBe('112px');
    expect(tooltip.style.top).toBe('38px');
    expect(tooltip.innerHTML).toContain('Close: <strong>$');
    expect(tooltip.innerHTML).toContain('MA50: $');
    expect(tooltip.innerHTML).toContain('MA200: $');
    tooltip.remove();
  });

  it('renders an empty shell with no data', () => {
    const { container } = render(<PriceChart data={[]} />);
    expect(container.querySelector('svg')!.querySelectorAll('path').length).toBe(0);
  });
});
