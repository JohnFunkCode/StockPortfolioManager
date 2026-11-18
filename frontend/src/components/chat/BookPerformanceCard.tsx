/**
 * Self-fetching chat card for the Sidekick: the caller's own real book's
 * return and drawdown against SPY, with realized P/L by source (#431 step
 * 5.3). No props ever: the figures come from the browser's authenticated
 * session, never from an `owner` or a book the model could name (the rule
 * `RealizedPnlCard` set, #126's decision 20), so it is always the real book.
 * The period pulldown is the card's own view state, opening on Year.
 *
 * It is the Portfolio page's Performance section under another title, so the
 * two share a query and every figure arrives computed (Rule 8.4). The link to
 * the Compare books view is left out: the chat is about the one book.
 */
import { Box } from '@mui/material';
import BookPerformance from '../portfolio/BookPerformance';

export default function BookPerformanceCard() {
  return (
    <Box data-testid="book-performance-card">
      <BookPerformance title="Your performance" showCompareLink={false} />
    </Box>
  );
}
