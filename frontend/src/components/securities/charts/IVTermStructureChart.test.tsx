import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import IVTermStructureChart from './IVTermStructureChart';
import { ivExpirations } from '../../../testUtils';

afterEach(cleanup);

describe('IVTermStructureChart', () => {
  it('plots the IV term curve across expirations', () => {
    const { container } = render(<IVTermStructureChart expirations={ivExpirations()} />);
    expect(container.querySelector('svg')!.querySelectorAll('path, circle').length).toBeGreaterThan(0);
  });
  it('degrades gracefully with no expirations', () => {
    const { container } = render(<IVTermStructureChart expirations={[]} />);
    // Empty term structure renders no data circles (svg may be absent entirely).
    const svg = container.querySelector('svg');
    if (svg) expect(svg.querySelectorAll('circle').length).toBe(0);
  });
});
