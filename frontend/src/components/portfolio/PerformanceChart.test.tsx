import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import PerformanceChart from './PerformanceChart';

const points = (values: Array<number | null>) =>
  values.map((value, i) => ({ date: `2026-11-0${i + 2}`, value }));

describe('PerformanceChart', () => {
  it('draws a line a book and the benchmark dashed, with a legend', () => {
    const { container } = render(
      <PerformanceChart
        lines={[
          { key: 'book', label: 'Real', points: points([1, 1.1, 1.21]) },
          { key: 'paper', label: 'Momentum', points: points([1, 0.9, null]) },
          { key: 'benchmark', label: 'SPY', benchmark: true, points: points([1, 1.05, 1.1]) },
        ]}
      />,
    );
    expect(container.querySelectorAll('path.book-line')).toHaveLength(2);
    expect(container.querySelectorAll('path.benchmark-line')).toHaveLength(1);
    expect(container.querySelector('path.benchmark-line')).toHaveAttribute('stroke-dasharray', '5,4');
    expect(container.querySelector('line.start-line')).not.toBeNull();
    for (const label of ['Real', 'Momentum', 'SPY', 'Growth of $1']) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it('says there is nothing to chart when no point has a value', () => {
    render(<PerformanceChart lines={[{ key: 'book', label: 'Real', points: points([null]) }]} />);
    expect(screen.getByText('No valued sessions to chart yet.')).toBeInTheDocument();
    expect(screen.queryByTestId('performance-chart')).toBeNull();
  });
});
