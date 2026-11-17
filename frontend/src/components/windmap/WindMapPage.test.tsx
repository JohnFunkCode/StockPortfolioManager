import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import WindMapPage, { WIND_MAP_SRC } from './WindMapPage';

describe('WindMapPage', () => {
  it('renders the heading', () => {
    render(<WindMapPage />);
    expect(screen.getByRole('heading', { name: 'Wind Map' })).toBeInTheDocument();
  });

  it('frames the pre-built Wind Map page', () => {
    render(<WindMapPage />);
    const frame = screen.getByTitle('Sector Wind Map');
    expect(frame.tagName).toBe('IFRAME');
    expect(frame).toHaveAttribute('src', WIND_MAP_SRC);
    expect(WIND_MAP_SRC).toBe('/research/wind-map.html');
  });

  it('sandboxes the frame away from the QuantUI origin', () => {
    // allow-same-origin would hand the prototype's inline script QuantUI's
    // storage and BYOK vault; the server's sandbox CSP enforces the same.
    render(<WindMapPage />);
    const sandbox = screen.getByTitle('Sector Wind Map').getAttribute('sandbox');
    expect(sandbox).toBe('allow-scripts');
  });
});
