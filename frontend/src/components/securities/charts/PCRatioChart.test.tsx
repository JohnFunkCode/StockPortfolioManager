import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import PCRatioChart from './PCRatioChart';
import { pcHistoryRows } from '../../../testUtils';

afterEach(cleanup);

describe('PCRatioChart', () => {
  it('draws the P/C and price series', () => {
    const { container } = render(<PCRatioChart data={pcHistoryRows(30)} />);
    expect(container.querySelector('svg')!.querySelectorAll('path').length).toBeGreaterThan(0);
  });
  it('renders empty with no data', () => {
    const { container } = render(<PCRatioChart data={[]} />);
    expect(container.querySelector('svg')!.querySelectorAll('path').length).toBe(0);
  });
});
