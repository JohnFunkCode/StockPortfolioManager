/**
 * Chart suite pattern (frontend 85%-campaign, demo template):
 * D3 charts render fine in jsdom — feed indicatorRows(), assert the svg
 * gained real content (paths/lines/text) and the empty-data branch degrades.
 * Copy this shape for MACD/Volume/PCRatio/MaxPain/IVTermStructure/OptionsChain.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render } from '@testing-library/react';

import RSIChart from './RSIChart';
import { indicatorRows } from '../../../testUtils';

afterEach(cleanup);

describe('RSIChart', () => {
  it('draws the oscillator with bands from indicator data', () => {
    const { container } = render(<RSIChart data={indicatorRows(60)} />);
    const svg = container.querySelector('svg')!;
    expect(svg.querySelectorAll('path').length).toBeGreaterThan(0);   // RSI line
    expect(svg.querySelectorAll('line').length).toBeGreaterThan(0);   // 30/70 bands
    expect(svg.textContent).toBeTruthy();                             // axis labels
  });

  it('renders an empty shell when no data', () => {
    const { container } = render(<RSIChart data={[]} />);
    const svg = container.querySelector('svg')!;
    expect(svg.querySelectorAll('path').length).toBe(0);
  });

  it('skips rows without rsi values', () => {
    const rows = indicatorRows(30).map((r) => ({ ...r, rsi: null }));
    const { container } = render(<RSIChart data={rows as never} />);
    expect(container.querySelector('svg')!.querySelectorAll('path').length).toBe(0);
  });
});
