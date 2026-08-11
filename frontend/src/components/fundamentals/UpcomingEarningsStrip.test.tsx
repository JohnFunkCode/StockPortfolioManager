import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';

import UpcomingEarningsStrip from './UpcomingEarningsStrip';
import { mockApi, renderWithProviders, upcomingEarning, upcomingEarnings } from '../../testUtils';

afterEach(() => {
  vi.unstubAllGlobals();
});

const EARNINGS = '/api/securities/fundamentals/upcoming-earnings';

describe('UpcomingEarningsStrip', () => {
  it('shows a spinner while the request is in flight', () => {
    mockApi([[EARNINGS, upcomingEarnings()]]);
    renderWithProviders(<UpcomingEarningsStrip />);
    expect(screen.getByText(/Reading the earnings calendar/i)).toBeInTheDocument();
  });

  it('shows an error alert when the fetch fails', async () => {
    mockApi([[EARNINGS, { __status: 500, error: 'boom' }]]);
    renderWithProviders(<UpcomingEarningsStrip />);
    await waitFor(() =>
      expect(screen.getByText(/Couldn't load upcoming earnings/i)).toBeInTheDocument(),
    );
  });

  it('renders a box per symbol with the countdown and risk level', async () => {
    mockApi([
      [
        EARNINGS,
        upcomingEarnings([
          upcomingEarning('INTC', { days_to_earnings: 4, risk_level: 'elevated' }),
          upcomingEarning('WMT', { days_to_earnings: 0, risk_level: 'high' }),
        ]),
      ],
    ]);
    renderWithProviders(<UpcomingEarningsStrip />);
    await waitFor(() => expect(screen.getByTestId('upcoming-earnings-strip')).toBeInTheDocument());

    expect(within(screen.getByTestId('earnings-INTC')).getByText('in 4d')).toBeInTheDocument();
    expect(within(screen.getByTestId('earnings-INTC')).getByText('· elevated')).toBeInTheDocument();
    // Day-of reads "today", not "in 0d".
    expect(within(screen.getByTestId('earnings-WMT')).getByText('today')).toBeInTheDocument();
    expect(screen.getByText('2 scheduled')).toBeInTheDocument();
    expect(screen.getByText('Earnings · next 14d')).toBeInTheDocument();
  });

  it('surfaces how many stale dates were hidden', async () => {
    mockApi([[EARNINGS, upcomingEarnings([upcomingEarning('INTC')], { stale_excluded: 6 })]]);
    renderWithProviders(<UpcomingEarningsStrip />);
    await waitFor(() => expect(screen.getByText('6 stale hidden')).toBeInTheDocument());
  });

  it('hides the stale chip when nothing was excluded', async () => {
    mockApi([[EARNINGS, upcomingEarnings([upcomingEarning('INTC')])]]);
    renderWithProviders(<UpcomingEarningsStrip />);
    await waitFor(() => expect(screen.getByTestId('earnings-INTC')).toBeInTheDocument());
    expect(screen.queryByText(/stale hidden/)).not.toBeInTheDocument();
  });

  it('says the calendar is empty rather than rendering a blank strip', async () => {
    mockApi([[EARNINGS, upcomingEarnings([], { days_window: 7 })]]);
    renderWithProviders(<UpcomingEarningsStrip />);
    await waitFor(() =>
      expect(
        screen.getByText(/Nothing on the tracked list reports in the next 7 days/i),
      ).toBeInTheDocument(),
    );
  });

  it('omits the risk suffix when the server has no risk level', async () => {
    mockApi([[EARNINGS, upcomingEarnings([upcomingEarning('INTC', { risk_level: null })])]]);
    renderWithProviders(<UpcomingEarningsStrip />);
    await waitFor(() => expect(screen.getByTestId('earnings-INTC')).toBeInTheDocument());
    expect(screen.queryByText(/^· /)).not.toBeInTheDocument();
  });

  it('sends the tracked scope on the wire', async () => {
    const api = mockApi([[EARNINGS, upcomingEarnings()]]);
    renderWithProviders(<UpcomingEarningsStrip />);
    await waitFor(() => expect(screen.getByTestId('earnings-INTC')).toBeInTheDocument());
    expect(api.calls[0][0]).toContain('scope=tracked');
  });
});
