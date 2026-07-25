import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import VolumeChart from './VolumeChart';
import { indicatorRows } from '../../../testUtils';

afterEach(cleanup);

describe('VolumeChart', () => {
  it('draws volume bars from indicator data', () => {
    const { container } = render(<VolumeChart data={indicatorRows(60)} />);
    expect(container.querySelector('svg')!.querySelectorAll('rect').length).toBeGreaterThan(0);
  });
  it('renders empty with no data', () => {
    const { container } = render(<VolumeChart data={[]} />);
    expect(container.querySelector('svg')!.querySelectorAll('rect').length).toBe(0);
  });
});
