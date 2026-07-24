import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import MACDChart from './MACDChart';
import { indicatorRows } from '../../../testUtils';

afterEach(cleanup);

describe('MACDChart', () => {
  it('draws MACD/signal/histogram from indicator data', () => {
    const { container } = render(<MACDChart data={indicatorRows(60)} />);
    const svg = container.querySelector('svg')!;
    expect(svg.querySelectorAll('path, rect, line').length).toBeGreaterThan(0);
  });
  it('renders empty with no data', () => {
    const { container } = render(<MACDChart data={[]} />);
    expect(container.querySelector('svg')!.querySelectorAll('path').length).toBe(0);
  });
});
