import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClientProvider } from '@tanstack/react-query';

import App from './App';
import { AppThemeProvider } from './ThemeContext';
import { ChatProvider } from './chat/ChatContext';
import { KeyVaultProvider } from './vault/KeyVaultContext';
import { makeQueryClient, mockApi } from './testUtils';

function renderApp(route: string) {
  // App owns its own <Routes>, so drive it with a MemoryRouter initialEntries
  // and the full provider stack from main.tsx.
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <AppThemeProvider>
        <KeyVaultProvider>
          <ChatProvider>
            <MemoryRouter initialEntries={[route]}>
              <App />
            </MemoryRouter>
          </ChatProvider>
        </KeyVaultProvider>
      </AppThemeProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe('App routing', () => {
  it('renders the app shell with nav at the root', () => {
    mockApi([[/\/api\//, {}]]);
    renderApp('/');
    // The nav chrome (Dashboard link) is always present in the Layout.
    expect(screen.getAllByRole('link').length).toBeGreaterThan(0);
  });

  it('renders the securities route', async () => {
    mockApi([[/\/api\//, { securities: [] }]]);
    renderApp('/securities');
    await waitFor(() =>
      expect(screen.getAllByRole('link').length).toBeGreaterThan(0),
    );
  });

  it('shows the not-found fallback for an unknown route', () => {
    mockApi([[/\/api\//, {}]]);
    renderApp('/nonexistent-path');
    expect(screen.getByText('Page not found')).toBeInTheDocument();
  });
});
