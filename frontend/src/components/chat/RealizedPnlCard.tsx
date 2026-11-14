/**
 * Self-fetching chat card for the Sidekick: the caller's own realized P/L by
 * symbol (#235). Read-only, and no props ever: the sales come from the
 * browser's authenticated session, never from an `owner` or a book the model
 * could name (decision #20), so it is always the real book. The period
 * pulldown is the card's own view state, opening on Year.
 *
 * It is the Portfolio page's Realized section under another title, so the two
 * share a query and every figure arrives computed (Rule 8.4).
 */
import { Box } from '@mui/material';
import RealizedPnl from '../portfolio/RealizedPnl';

export default function RealizedPnlCard() {
  return (
    <Box data-testid="realized-pnl-card">
      <RealizedPnl title="Your realized P/L" />
    </Box>
  );
}
