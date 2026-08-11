import { describe, expect, it } from 'vitest';

import {
  buildNavSections,
  isPathActive,
  navItems,
  navSections,
  routes,
  MY_POSITIONS,
  RESEARCH,
  type NavRoute,
} from './navigation';

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
    expect(hidden).toEqual(['/plans/:id', '/securities/:symbol']);
  });

  it('no longer carries the retired /symbols page', () => {
    // Issue #147 Part G1: it duplicated /securities three columns out of four,
    // and /plans the fourth. The REST routes stay; the page is gone.
    expect(routes.map((route) => route.path)).not.toContain('/symbols');
  });
});

describe('navItems', () => {
  it('is every route except the detail routes', () => {
    expect(navItems.map((item) => item.path)).toEqual([
      '/', '/plans', '/harvester', '/securities', '/watchlist', '/fundamentals', '/arbitrage',
      '/settings',
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

describe('navSections', () => {
  it('renders two menus and leaves Settings top-level', () => {
    expect(
      navSections.map((section) =>
        section.kind === 'group'
          ? [section.label, section.items.map((item) => item.label)]
          : ['', section.route.label],
      ),
    ).toEqual([
      [MY_POSITIONS, ['Portfolio', 'Plans', 'Harvester']],
      [RESEARCH, ['Securities', 'Watchlist', 'Fundamentals', 'Arbitrage']],
      ['', 'Settings'],
    ]);
  });

  it('covers every nav item exactly once', () => {
    const flattened = navSections.flatMap((section) =>
      section.kind === 'group' ? section.items : [section.route],
    );
    expect(flattened.map((item) => item.path).sort())
      .toEqual(navItems.map((item) => item.path).sort());
  });

  it('joins a later page to an existing menu rather than opening a second one', () => {
    // `routes` is sorted today, so only a synthetic interleaving can tell
    // label-keyed grouping apart from adjacency-keyed grouping.
    const icon = <span />;
    const item = (path: string, group?: string): NavRoute =>
      ({ path, label: path, group, icon, element: <span /> });

    const sections = buildNavSections([
      item('/a', 'One'), item('/b', 'Two'), item('/c', 'One'), item('/d'),
    ]);

    expect(sections.map((s) => (s.kind === 'group' ? s.label : s.route.path)))
      .toEqual(['One', 'Two', '/d']);
    expect(
      sections.flatMap((s) => (s.kind === 'group' && s.label === 'One' ? s.items : []))
        .map((i) => i.path),
    ).toEqual(['/a', '/c']);
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
