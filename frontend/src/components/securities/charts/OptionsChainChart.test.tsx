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
});
