/**
 * The `/fundamentals` page — "what's good?" across the tracked universe
 * (issue #147 Part D).
 *
 * Five independent reads, one per panel, all `scope=tracked` and all
 * cache-backed. There is deliberately **no page-level loading gate**: each
 * panel owns its own loading and error state, so a slow score-changes query
 * (the only one that runs a per-symbol history read) never holds the rankings
 * back, and a failure in one panel leaves the other four standing. That is the
 * opposite of the nightly report this replaces, which was one artifact that
 * either rendered or did not.
 *
 * "Tracked" is the shared watchlist plus every owner's positions — the same
 * universe `main.py` captures options for and warms fundamentals over, so the
 * page ranks exactly the set the cache is being kept fresh for.
 */
import { Box, Stack, Tooltip, Typography } from '@mui/material';
import CacheFreshnessBanner from './CacheFreshnessBanner';
import TopFundamentalsPanel from './TopFundamentalsPanel';
import ScoreChangesPanel from './ScoreChangesPanel';
import SectorBreakdownPanel from './SectorBreakdownPanel';
import UpcomingEarningsStrip from './UpcomingEarningsStrip';
import { MUTED } from './scoreLabels';

export default function FundamentalsPage() {
  return (
    <Box>
      <Stack direction="row" spacing={1.5} alignItems="baseline" sx={{ mb: 1 }}>
        <Typography variant="h4">Fundamentals</Typography>
        <Tooltip title="The shared watchlist plus every owner's positions — the same universe the nightly job warms">
          <Typography variant="body2" sx={{ color: MUTED }}>
            tracked universe
          </Typography>
        </Tooltip>
      </Stack>

      <CacheFreshnessBanner />

      <Stack spacing={2}>
        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: { xs: '1fr', lg: '1fr 1fr' },
            gap: 2,
            alignItems: 'start',
          }}
        >
          <TopFundamentalsPanel />
          <ScoreChangesPanel />
        </Box>
        <SectorBreakdownPanel />
        <UpcomingEarningsStrip />
      </Stack>
    </Box>
  );
}
