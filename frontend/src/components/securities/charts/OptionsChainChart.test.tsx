import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import OptionsChainChart from './OptionsChainChart';
import { optionsSnapshot } from '../../../testUtils';

afterEach(cleanup);

describe('OptionsChainChart', () => {
  it('renders the OI ladder and narrative for a snapshot', () => {
    const { container } = render(<OptionsChainChart snapshot={optionsSnapshot()} />);
    expect(container.querySelector('svg')!.querySelectorAll('rect').length).toBeGreaterThan(0);
    expect(container.textContent).toMatch(/contract|call|put/i);
  });

  it('degrades gracefully when the nearest expiration has no contracts', () => {
    const snap = optionsSnapshot();
    snap.expirations[0].contracts = [];
    const { container } = render(<OptionsChainChart snapshot={snap} />);
    // No contracts -> no OI bars, but the component still renders its shell.
    expect(container).toBeTruthy();
  });
});
