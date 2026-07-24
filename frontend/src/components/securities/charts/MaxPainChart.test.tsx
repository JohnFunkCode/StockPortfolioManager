import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import MaxPainChart from './MaxPainChart';
import { painCurve } from '../../../testUtils';

afterEach(cleanup);

describe('MaxPainChart', () => {
  it('draws pain bars and the max-pain marker', () => {
    const { container } = render(
      <MaxPainChart painCurve={painCurve()} currentPrice={102} maxPainStrike={100} />,
    );
    expect(container.querySelector('svg')!.querySelectorAll('rect').length).toBeGreaterThan(0);
  });
  it('renders nothing with no curve', () => {
    const { container } = render(
      <MaxPainChart painCurve={[]} currentPrice={100} maxPainStrike={null} />,
    );
    expect(container.querySelector('svg')).toBeNull();
  });
});
