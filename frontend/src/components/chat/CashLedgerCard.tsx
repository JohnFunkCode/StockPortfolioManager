/**
 * Self-fetching chat card for the Sidekick: the cash in the caller's own real
 * book (#431 step 3.1). No props ever: the ledger comes from the browser's
 * authenticated session, never from an `owner` or a book the model could name
 * (the rule `RealizedPnlCard` set for #235; it is #126's decision 20, not
 * this plan's), so it is always the real book.
 *
 * Read-only: Sidekick tools change nothing, so the card shows the ledger and
 * leaves the entry forms to the Portfolio page. It is that page's Cash
 * section under another title, so the two share a query and every figure
 * arrives computed (Rule 8.4).
 */
import { Box } from '@mui/material';
import CashLedger from '../portfolio/CashLedger';

export default function CashLedgerCard() {
  return (
    <Box data-testid="cash-ledger-card">
      <CashLedger title="Your cash" readOnly />
    </Box>
  );
}
