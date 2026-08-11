import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';

import NavBar from './NavBar';
import { AppThemeProvider } from '../../ThemeContext';
import { ChatProvider } from '../../chat/ChatContext';

/** Shows where the router actually went, so a click can be checked. */
function Where() {
  return <div data-testid="where">{useLocation().pathname}</div>;
}

function renderNav(route: string) {
  // `useChat` throws outside ChatProvider, and the bar reads the theme name.
  return render(
    <AppThemeProvider>
      <ChatProvider>
        <MemoryRouter initialEntries={[route]}>
          <NavBar />
          <Where />
        </MemoryRouter>
      </ChatProvider>
    </AppThemeProvider>,
  );
}

/** The dropdown trigger — a button, not a link, so it is unambiguous. */
const trigger = (name: string) => screen.getByRole('button', { name: new RegExp(name) });

afterEach(cleanup);

describe('NavBar', () => {
  it('shows only the two menus and Settings until a menu is opened', () => {
    renderNav('/');
    expect(trigger('My Positions')).toBeInTheDocument();
    expect(trigger('Research')).toBeInTheDocument();
    // Settings is deliberately ungrouped: one click, not two.
    expect(screen.getByRole('link', { name: /Settings/ })).toHaveAttribute('href', '/settings');
    // A grouped page is not reachable in one click.
    expect(screen.queryByRole('link', { name: /Watchlist/ })).not.toBeInTheDocument();
  });

  it('reveals all four Research pages when the menu is opened', async () => {
    const user = userEvent.setup();
    renderNav('/');
    await user.click(trigger('Research'));

    const menu = await screen.findByRole('menu');
    expect(within(menu).getAllByRole('menuitem').map((item) => item.textContent)).toEqual([
      'Securities', 'Watchlist', 'Fundamentals', 'Arbitrage',
    ]);
  });

  it('navigates on selection and closes the menu behind itself', async () => {
    const user = userEvent.setup();
    renderNav('/');
    await user.click(trigger('My Positions'));
    await user.click(await screen.findByRole('menuitem', { name: 'Harvester' }));

    expect(screen.getByTestId('where')).toHaveTextContent('/harvester');
    // Without the onClick that clears the anchor, the popover would sit over
    // the page it just navigated to.
    await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument());
  });

  it('lights up only the menu holding the current page', () => {
    renderNav('/watchlist');
    expect(trigger('Research')).toHaveAttribute('aria-current', 'true');
    expect(trigger('My Positions')).not.toHaveAttribute('aria-current');
    expect(screen.getByRole('link', { name: /Settings/ })).not.toHaveAttribute('aria-current');
  });

  it('lights up Settings on its own page and neither menu', () => {
    renderNav('/settings');
    expect(screen.getByRole('link', { name: /Settings/ })).toHaveAttribute('aria-current', 'page');
    expect(trigger('Research')).not.toHaveAttribute('aria-current');
    expect(trigger('My Positions')).not.toHaveAttribute('aria-current');
  });

  it('marks the open menu item for the page it is on', async () => {
    const user = userEvent.setup();
    renderNav('/plans/7');
    // A drill-down keeps its parent lit — /plans/:id has no button of its own.
    expect(trigger('My Positions')).toHaveAttribute('aria-current', 'true');

    await user.click(trigger('My Positions'));
    expect(await screen.findByRole('menuitem', { name: 'Plans' }))
      .toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('menuitem', { name: 'Harvester' }))
      .not.toHaveAttribute('aria-current');
  });

  it('flips the theme icon when the theme is toggled', async () => {
    const user = userEvent.setup();
    renderNav('/');
    // Dark is the default, so the control offers the way out of it.
    expect(screen.getByTestId('LightModeIcon')).toBeInTheDocument();

    await user.click(screen.getByTestId('LightModeIcon'));
    expect(await screen.findByTestId('DarkModeIcon')).toBeInTheDocument();
  });
});
