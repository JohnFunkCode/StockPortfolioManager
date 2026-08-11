/**
 * The single source of truth for what pages QuantUI has.
 *
 * `App.tsx` used to carry two parallel lists — a `navItems` array for the nav
 * buttons and a `<Routes>` block for the router — so adding a page meant two
 * edits in two places, and it was possible for them to disagree. Both render
 * sites now map over `routes` below, which makes adding a page a one-object
 * append (issue #147, seam PR; see
 * docs/proposals/legacy-report-retirement-plan.md).
 *
 * `group` drives the nav bar's dropdown menus (Part G2). An entry without one
 * stays a top-level button — `Settings` is neither a position nor research,
 * and costs one click rather than two.
 */
import type { ReactElement } from 'react';
import DashboardIcon from '@mui/icons-material/Dashboard';
import AccountBalanceWalletIcon from '@mui/icons-material/AccountBalanceWallet';
import ListAltIcon from '@mui/icons-material/ListAlt';
import BarChartIcon from '@mui/icons-material/BarChart';
import CompareArrowsIcon from '@mui/icons-material/CompareArrows';
import VisibilityIcon from '@mui/icons-material/Visibility';
import AssessmentIcon from '@mui/icons-material/Assessment';
import SettingsIcon from '@mui/icons-material/Settings';
import DashboardPage from './components/dashboard/DashboardPage';
import PortfolioPage from './components/portfolio/PortfolioPage';
import PlansPage from './components/plans/PlansPage';
import PlanDetailPage from './components/plans/PlanDetailPage';
import SecuritiesPage from './components/securities/SecuritiesPage';
import SecurityDetailPage from './components/securities/SecurityDetailPage';
import ArbitragePage from './components/arbitrage/ArbitragePage';
import WatchlistPage from './components/watchlist/WatchlistPage';
import FundamentalsPage from './components/fundamentals/FundamentalsPage';
import SettingsPage from './components/settings/SettingsPage';

/** The two nav menus. Named so a typo is a compile error, not a third menu. */
export const MY_POSITIONS = 'My Positions';
export const RESEARCH = 'Research';

type BaseRoute = {
  path: string;
  label: string;
  element: ReactElement;
  /** Which nav menu this page sits under. Ungrouped entries stay top-level. */
  group?: string;
};

/** A destination that appears in the nav bar. The icon is required there. */
export type NavRoute = BaseRoute & { icon: ReactElement; nav?: true };

/** Reached by drill-down (`/plans/:id`), never shown in the bar. */
export type DetailRoute = BaseRoute & { nav: false; icon?: never };

export type AppRoute = NavRoute | DetailRoute;

/**
 * Every route under `Layout`, in nav-bar order. The `*` fallback is not here:
 * it is not a destination, and `App.tsx` renders it last on its own.
 */
export const routes: AppRoute[] = [
  { path: '/', label: 'Portfolio', group: MY_POSITIONS, icon: <AccountBalanceWalletIcon />, element: <PortfolioPage /> },
  { path: '/plans', label: 'Plans', group: MY_POSITIONS, icon: <ListAltIcon />, element: <PlansPage /> },
  { path: '/plans/:id', label: 'Plan detail', nav: false, element: <PlanDetailPage /> },
  { path: '/harvester', label: 'Harvester', group: MY_POSITIONS, icon: <DashboardIcon />, element: <DashboardPage /> },
  { path: '/securities', label: 'Securities', group: RESEARCH, icon: <BarChartIcon />, element: <SecuritiesPage /> },
  { path: '/securities/:symbol', label: 'Security detail', nav: false, element: <SecurityDetailPage /> },
  { path: '/watchlist', label: 'Watchlist', group: RESEARCH, icon: <VisibilityIcon />, element: <WatchlistPage /> },
  { path: '/fundamentals', label: 'Fundamentals', group: RESEARCH, icon: <AssessmentIcon />, element: <FundamentalsPage /> },
  { path: '/arbitrage', label: 'Arbitrage', group: RESEARCH, icon: <CompareArrowsIcon />, element: <ArbitragePage /> },
  { path: '/settings', label: 'Settings', icon: <SettingsIcon />, element: <SettingsPage /> },
];

/** The subset of `routes` that gets a button in the nav bar. */
export const navItems: NavRoute[] = routes.filter(
  (route): route is NavRoute => route.nav !== false,
);

/**
 * One nav-bar control: either a lone button, or a group button that opens a
 * menu of its children.
 */
export type NavSection =
  | { kind: 'item'; route: NavRoute }
  | { kind: 'group'; label: string; items: NavRoute[] };

/**
 * Groups `items` by their `group` label, in first-appearance order, leaving
 * ungrouped entries as lone buttons where they sit.
 *
 * Grouping by label rather than by adjacency is deliberate: a page appended to
 * the middle of `routes` with an existing group's label joins that group,
 * instead of opening a second menu with the same name. Exported only so a test
 * can exercise that interleaving — today's `routes` happens to be sorted, so
 * `navSections` alone cannot tell the two implementations apart.
 */
export function buildNavSections(items: NavRoute[]): NavSection[] {
  const sections: NavSection[] = [];
  const groups = new Map<string, Extract<NavSection, { kind: 'group' }>>();

  for (const route of items) {
    if (route.group === undefined) {
      sections.push({ kind: 'item', route });
      continue;
    }
    const existing = groups.get(route.group);
    if (existing) {
      // `sections` holds this same object, so the push is visible there.
      existing.items.push(route);
      continue;
    }
    const group: Extract<NavSection, { kind: 'group' }> = {
      kind: 'group',
      label: route.group,
      items: [route],
    };
    groups.set(route.group, group);
    sections.push(group);
  }

  return sections;
}

/** What the nav bar renders, left to right. */
export const navSections: NavSection[] = buildNavSections(navItems);

/**
 * Whether `pathname` is on (or beneath) `path`.
 *
 * The `'/'` guard matters: Portfolio is the index route, so a plain
 * `startsWith` would light it up on every page.
 */
export function isPathActive(path: string, pathname: string): boolean {
  return pathname === path || (path !== '/' && pathname.startsWith(path));
}
