/**
 * PortfolioCheckIdeasCard — Buy-zone industries the caller doesn't hold, with
 * their largest S&P 500 members, and the 49 industries' current ranks.
 * Self-fetching and prop-less (`portfolio_check_ideas` in the Sidekick): the
 * ideas depend on the signed-in user's own holdings, so never add an owner prop.
 *
 * Display only: every number comes from GET /api/portfolio/check (Rule 8.4).
 */
import { useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Collapse,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import { usePortfolioCheck } from '../../hooks/usePortfolioCheck';
import { signedPercent } from './PortfolioCheckCard';

const LABEL_COLOR: Record<string, 'success' | 'warning' | 'error' | 'default'> = {
  'Buy zone': 'success',
  Watch: 'warning',
  Sell: 'error',
  Hold: 'default',
};

export default function PortfolioCheckIdeasCard() {
  const { data, isLoading, error } = usePortfolioCheck();
  const [ranksOpen, setRanksOpen] = useState(false);

  if (isLoading) {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 2 }}>
        <CircularProgress size={18} />
        <Typography variant="body2" color="text.secondary">Loading industry ideas…</Typography>
      </Box>
    );
  }
  if (error || !data) {
    return <Alert severity="error">Couldn&apos;t load the industry ideas.</Alert>;
  }

  return (
    <Box data-testid="portfolio-check-ideas-card">
      <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
        Strong industries you don&apos;t hold
      </Typography>
      {data.ideas.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          You already hold every industry in the Buy zone.
        </Typography>
      ) : (
        <Stack spacing={0.75}>
          {data.ideas.map((idea) => (
            <Box key={idea.industry_code} data-testid={`idea-${idea.industry_code}`}>
              <Typography variant="body2">
                <strong>{idea.industry}</strong> · rank {idea.rank} · {signedPercent(idea.wind12)} vs market
                over 12 months
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {idea.examples.length === 0
                  ? 'No S&P 500 members in this industry'
                  : idea.examples
                      .map((e) => `${e.ticker} (${e.name}${e.vs_peers_12m != null ? `; ${signedPercent(e.vs_peers_12m)} vs peers` : ''})`)
                      .join(', ')}
              </Typography>
            </Box>
          ))}
        </Stack>
      )}

      <Button size="small" sx={{ mt: 1 }} onClick={() => setRanksOpen((o) => !o)} aria-expanded={ranksOpen}>
        {ranksOpen ? 'Hide' : 'Show'} all {data.industries.length} industry ranks
      </Button>
      <Collapse in={ranksOpen} unmountOnExit>
        <Paper variant="outlined" sx={{ overflowX: 'auto', mt: 1 }}>
          <Table size="small" aria-label="Industry ranks">
            <TableHead>
              <TableRow>
                <TableCell align="right">Rank</TableCell>
                <TableCell>Industry</TableCell>
                <TableCell>Label</TableCell>
                <TableCell align="right">12-mo wind</TableCell>
                <TableCell align="right">5-yr wind</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.industries.map((i) => (
                <TableRow key={i.code}>
                  <TableCell align="right">{i.rank}</TableCell>
                  <TableCell>{i.industry}</TableCell>
                  <TableCell>
                    <Chip size="small" variant="outlined" label={i.label} color={LABEL_COLOR[i.label] ?? 'default'} />
                  </TableCell>
                  <TableCell align="right">{signedPercent(i.wind12)}</TableCell>
                  <TableCell align="right">{signedPercent(i.wind60)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Paper>
      </Collapse>
    </Box>
  );
}
