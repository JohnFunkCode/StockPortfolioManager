import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import SandboxedReport, { REPORT_SANDBOX } from './SandboxedReport';

describe('SandboxedReport', () => {
  const renderReport = () =>
    render(
      <SandboxedReport title="A Report" description="What it shows." src="/research/x.html" frameTitle="X frame">
        <p>notice</p>
      </SandboxedReport>,
    );

  it('renders the heading, description and children', () => {
    renderReport();
    expect(screen.getByRole('heading', { name: 'A Report' })).toBeInTheDocument();
    expect(screen.getByText('What it shows.')).toBeInTheDocument();
    expect(screen.getByText('notice')).toBeInTheDocument();
  });

  it('frames the page sandboxed away from the QuantUI origin', () => {
    // allow-same-origin would hand the framed page's script QuantUI's storage
    // and BYOK vault; the server's sandbox CSP (csp.mjs) enforces the same.
    renderReport();
    const frame = screen.getByTitle('X frame');
    expect(frame.tagName).toBe('IFRAME');
    expect(frame).toHaveAttribute('src', '/research/x.html');
    expect(frame.getAttribute('sandbox')).toBe(REPORT_SANDBOX);
    expect(REPORT_SANDBOX).toBe('allow-scripts');
    expect(frame).toHaveAttribute('referrerpolicy', 'no-referrer');
  });
});
