import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { act, cleanup, render, screen } from '@testing-library/react';

import { AppThemeProvider, useAppTheme } from './ThemeContext';
import { darkTheme, lightTheme, themes } from './themes';

function ThemeProbe() {
  const { themeName, setThemeName } = useAppTheme();
  return (
    <div>
      <span data-testid="name">{themeName}</span>
      <button onClick={() => setThemeName('light')}>light</button>
      <button onClick={() => setThemeName('dark')}>dark</button>
    </div>
  );
}

beforeEach(() => localStorage.clear());
afterEach(cleanup);

describe('theme objects', () => {
  it('dark and light build distinct palettes', () => {
    expect(themes.dark).toBe(darkTheme);
    expect(themes.light).toBe(lightTheme);
    expect(darkTheme.palette.mode).toBe('dark');
    expect(lightTheme.palette.mode).toBe('light');
  });
});

describe('AppThemeProvider', () => {
  it('defaults to dark and toggles + persists to localStorage', () => {
    render(
      <AppThemeProvider>
        <ThemeProbe />
      </AppThemeProvider>,
    );
    expect(screen.getByTestId('name').textContent).toBe('dark');

    act(() => screen.getByText('light').click());
    expect(screen.getByTestId('name').textContent).toBe('light');
    expect(localStorage.getItem('hl-theme')).toBe('light');
  });

  it('restores a persisted light preference on mount', () => {
    localStorage.setItem('hl-theme', 'light');
    render(
      <AppThemeProvider>
        <ThemeProbe />
      </AppThemeProvider>,
    );
    expect(screen.getByTestId('name').textContent).toBe('light');
  });
});
