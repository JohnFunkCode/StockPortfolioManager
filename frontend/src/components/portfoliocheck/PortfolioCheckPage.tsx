/**
 * PortfolioCheckPage — the quarterly Portfolio Check, under Research.
 *
 * Runs the signed-in user's own open lots through the Wind Map's tested
 * industry rules (sell / watch / hold / buy), adjusted for position size and
 * taxes (docs/proposals/portfolio-check-plan.md, step 4). Composed entirely of
 * the two cards the Sidekick also renders, so the two surfaces can't drift.
 */
import { Box, Paper, Stack, Typography } from '@mui/material';
import PortfolioCheckCard from './PortfolioCheckCard';
import PortfolioCheckIdeasCard from './PortfolioCheckIdeasCard';
import { usePortfolioCheck } from '../../hooks/usePortfolioCheck';
import { formatDate, formatPercent } from '../../utils/formatting';

function DataAsOf() {
  const { data } = usePortfolioCheck();
  if (!data) return null;
  const m = data.market_state;
  return (
    <Typography variant="caption" color="text.secondary" component="p" sx={{ mb: 2 }}>
      Industry signals as of {m.industries_asof}; stock-vs-peers window {formatDate(m.window.a)} to{' '}
      {formatDate(m.window.b)}; market data refreshed {formatDate(m.generated)}. Taxes assume{' '}
      {formatPercent(data.params.st_rate, 0)} short-term and {formatPercent(data.params.lt_rate, 0)} long-term.
    </Typography>
  );
}

export default function PortfolioCheckPage() {
  return (
    <Box>
      <Typography variant="h4" sx={{ mb: 1 }}>
        Portfolio Check
      </Typography>
      <Typography variant="body2" sx={{ mb: 0.5, color: 'text.secondary' }}>
        A quarterly check of each holding against the Wind Map&apos;s industry rules, adjusted for position size
        and taxes. Research output, not financial advice.
      </Typography>
      <DataAsOf />
      <Stack spacing={2}>
        <Paper variant="outlined" sx={{ p: 2 }}>
          <PortfolioCheckCard />
        </Paper>
        <Paper variant="outlined" sx={{ p: 2 }}>
          <PortfolioCheckIdeasCard />
        </Paper>
      </Stack>
    </Box>
  );
}
