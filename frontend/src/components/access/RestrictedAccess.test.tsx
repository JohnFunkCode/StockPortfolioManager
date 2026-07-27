import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

import RestrictedAccess, {
  RESTRICTED_ACCESS_REDIRECT_DELAY_MS,
  RESTRICTED_ACCESS_REDIRECT_URL,
} from './RestrictedAccess';

const COPY =
  'This system is restricted to authorized users. Individuals who attempt ' +
  'unauthorized access will be prosecuted. If you are unauthorized, ' +
  'terminate access now.';

function stubLocation() {
  const original = window.location;
  // window.location isn't directly assignable; replace the whole property
  // instead so `.href = ...` writes land on a throwaway stand-in object.
  Object.defineProperty(window, 'location', {
    value: { ...original, href: '' },
    writable: true,
    configurable: true,
  });
  return () => {
    Object.defineProperty(window, 'location', {
      value: original,
      writable: true,
      configurable: true,
    });
  };
}

describe('RestrictedAccess', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    cleanup();
  });

  it('renders the exact restricted-access copy', () => {
    render(<RestrictedAccess />);
    expect(screen.getByText(COPY)).toBeInTheDocument();
  });

  it('does not display any identifying information', () => {
    render(<RestrictedAccess />);
    expect(screen.queryByText(/@/)).not.toBeInTheDocument();
    expect(screen.queryByText(/contact/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/email/i)).not.toBeInTheDocument();
  });

  it('does not redirect before 10 seconds', () => {
    const restore = stubLocation();
    render(<RestrictedAccess />);
    vi.advanceTimersByTime(9_999);
    expect(window.location.href).toBe('');
    restore();
  });

  it('redirects to the FBI cyber crime page after 10 seconds', () => {
    const restore = stubLocation();
    render(<RestrictedAccess />);
    vi.advanceTimersByTime(RESTRICTED_ACCESS_REDIRECT_DELAY_MS);
    expect(window.location.href).toBe(RESTRICTED_ACCESS_REDIRECT_URL);
    restore();
  });
});
