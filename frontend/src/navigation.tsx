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
 * `group` is unused today. Part G2 of that plan collapses the flat button row
 * into dropdown menus and derives them from this field; it is declared here so
 * that change is a render-site change rather than another list rewrite.
 */
import type { ReactElement } from 'react';
import DashboardIcon from '@mui/icons-material/Dashboard';
import AccountBalanceWalletIcon from '@mui/icons-material/AccountBalanceWallet';
import ListAltIcon from '@mui/icons-material/ListAlt';
import ShowChartIcon from '@mui/icons-material/ShowChart';
import BarChartIcon from '@mui/icons-material/BarChart';
import CompareArrowsIcon from '@mui/icons-material/CompareArrows';
import VisibilityIcon from '@mui/icons-material/Visibility';
import AssessmentIcon from '@mui/icons-material/Assessment';
import SettingsIcon from '@mui/icons-material/Settings';
import DashboardPage from './components/dashboard/DashboardPage';
import PortfolioPage from './components/portfolio/PortfolioPage';
import PlansPage from './components/plans/PlansPage';
import PlanDetailPage from './components/plans/PlanDetailPage';
import SymbolsPage from './components/symbols/SymbolsPage';
import SecuritiesPage from './components/securities/SecuritiesPage';
import SecurityDetailPage from './components/securities/SecurityDetailPage';
import ArbitragePage from './components/arbitrage/ArbitragePage';
import WatchlistPage from './components/watchlist/WatchlistPage';
import FundamentalsPage from './components/fundamentals/FundamentalsPage';
import SettingsPage from './components/settings/SettingsPage';

type BaseRoute = {
  path: string;
  label: string;
  element: ReactElement;
  /** Nav grouping, for Part G2. Ungrouped entries stay top-level. */
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
  { path: '/', label: 'Portfolio', icon: <AccountBalanceWalletIcon />, element: <PortfolioPage /> },
  { path: '/harvester', label: 'Harvester', icon: <DashboardIcon />, element: <DashboardPage /> },
  { path: '/securities', label: 'Securities', icon: <BarChartIcon />, element: <SecuritiesPage /> },
  { path: '/securities/:symbol', label: 'Security detail', nav: false, element: <SecurityDetailPage /> },
  { path: '/watchlist', label: 'Watchlist', icon: <VisibilityIcon />, element: <WatchlistPage /> },
  { path: '/fundamentals', label: 'Fundamentals', icon: <AssessmentIcon />, element: <FundamentalsPage /> },
  { path: '/arbitrage', label: 'Arbitrage', icon: <CompareArrowsIcon />, element: <ArbitragePage /> },
  { path: '/plans', label: 'Plans', icon: <ListAltIcon />, element: <PlansPage /> },
  { path: '/plans/:id', label: 'Plan detail', nav: false, element: <PlanDetailPage /> },
  { path: '/symbols', label: 'Symbols', icon: <ShowChartIcon />, element: <SymbolsPage /> },
  { path: '/settings', label: 'Settings', icon: <SettingsIcon />, element: <SettingsPage /> },
];

/** The subset of `routes` that gets a button in the nav bar. */
export const navItems: NavRoute[] = routes.filter(
  (route): route is NavRoute => route.nav !== false,
);

/**
 * Whether `pathname` is on (or beneath) `path`.
 *
 * The `'/'` guard matters: Portfolio is the index route, so a plain
 * `startsWith` would light it up on every page.
 */
export function isPathActive(path: string, pathname: string): boolean {
  return pathname === path || (path !== '/' && pathname.startsWith(path));
}
