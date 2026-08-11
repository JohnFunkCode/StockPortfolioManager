import { describe, expect, it } from 'vitest';

import { isPathActive, navItems, routes } from './navigation';

describe('routes', () => {
  it('has no duplicate paths', () => {
    const paths = routes.map((route) => route.path);
    expect(new Set(paths).size).toBe(paths.length);
  });

  it('keeps "/" as the index route', () => {
    expect(routes[0].path).toBe('/');
  });

  it('gives every route an element', () => {
    for (const route of routes) {
      expect(route.element, route.path).toBeTruthy();
    }
  });

  it('hides the drill-down detail routes from the nav bar', () => {
    const hidden = routes.filter((route) => route.nav === false).map((r) => r.path);
    expect(hidden).toEqual(['/securities/:symbol', '/plans/:id']);
  });
});

describe('navItems', () => {
  it('is every route except the detail routes', () => {
    expect(navItems.map((item) => item.path)).toEqual([
      '/', '/harvester', '/securities', '/watchlist', '/fundamentals', '/arbitrage', '/plans',
      '/symbols', '/settings',
    ]);
  });

  it('gives every nav button a label and an icon', () => {
    for (const item of navItems) {
      expect(item.label, item.path).toBeTruthy();
      expect(item.icon, item.path).toBeTruthy();
    }
  });

  it('never puts a parameterized path in the bar', () => {
    for (const item of navItems) {
      expect(item.path, item.path).not.toContain(':');
    }
  });
});

describe('isPathActive', () => {
  it('matches the exact path', () => {
    expect(isPathActive('/plans', '/plans')).toBe(true);
  });

  it('matches a child path', () => {
    expect(isPathActive('/plans', '/plans/7')).toBe(true);
  });

  it('does not match an unrelated path', () => {
    expect(isPathActive('/plans', '/securities')).toBe(false);
  });

  it('only lights up the index route on the index itself', () => {
    // Without the '/' guard, startsWith would make Portfolio active everywhere.
    expect(isPathActive('/', '/')).toBe(true);
    expect(isPathActive('/', '/securities')).toBe(false);
  });
});
