/**
 * PortfolioCheckCard — the caller's holdings, each with its Portfolio Check
 * action, the reasons for it and a tax note. Self-fetching and prop-less, so
 * the Sidekick can render it (`portfolio_check`) exactly as the page does; it
 * always shows the signed-in user's own lots — never add an owner prop.
 *
 * Display only: every number comes from GET /api/portfolio/check, computed in
 * quantcore/analytics/portfolio_check.py. No math here (Rule 8.4).
 */
import { useState } from 'react';
import {
  Alert,
  Box,
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
import type { PortfolioCheckHolding } from '../../api/portfolioCheck';
import { formatCurrency, formatPercent } from '../../utils/formatting';

type ChipColor = 'error' | 'warning' | 'success' | 'info' | 'default';

/** The chip colour for an action. "Sell after <date>" is still a sell. */
export function actionColor(action: string): ChipColor {
  if (action.startsWith('Sell')) return 'error';
  if (action === 'Hold, on watch' || action === 'Review') return 'warning';
  if (action === 'Buy / add') return 'success';
  if (action === 'Hold (at size limit)') return 'info';
  return 'default';
}

/** A server-computed fraction as a signed percent, `+12%`. Formatting only. */
export function signedPercent(value: number | null): string {
  if (value == null) return '—';
  const pct = Math.round(value * 100);
  return `${pct > 0 ? '+' : pct < 0 ? '−' : ''}${Math.abs(pct)}%`;
}

function HoldingRow({ h, total }: { h: PortfolioCheckHolding; total: number }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <TableRow
        hover
        onClick={() => setOpen((o) => !o)}
        sx={{ cursor: 'pointer', '& > td': { borderBottom: open ? 'none' : undefined } }}
        aria-expanded={open}
        data-testid={`check-row-${h.ticker}`}
      >
        <TableCell>
          <Chip size="small" label={h.action} color={actionColor(h.action)} variant="outlined" />
        </TableCell>
        <TableCell>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>{h.ticker}</Typography>
          {h.company && (
            <Typography variant="caption" color="text.secondary">{h.company}</Typography>
          )}
        </TableCell>
        <TableCell>
          {h.industry}
          {h.industry_rank != null && (
            <Typography variant="caption" color="text.secondary" component="div">
              rank {h.industry_rank} of {total}
            </Typography>
          )}
        </TableCell>
        <TableCell align="right">{formatPercent(h.weight, 1)}</TableCell>
        <TableCell align="right">{signedPercent(h.vs_peers_12m)}</TableCell>
        <TableCell align="right">{formatCurrency(h.value)}</TableCell>
      </TableRow>
      <TableRow>
        <TableCell colSpan={6} sx={{ py: 0 }}>
          <Collapse in={open} unmountOnExit>
            <Box sx={{ py: 1.5 }} data-testid={`check-details-${h.ticker}`}>
              <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
                {h.reasons.map((r) => (
                  <Typography component="li" variant="body2" key={r} sx={{ mb: 0.5 }}>
                    {r}
                  </Typography>
                ))}
              </Box>
              {h.tax_note && (
                <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                  <strong>Tax:</strong> {h.tax_note}
                </Typography>
              )}
            </Box>
          </Collapse>
        </TableCell>
      </TableRow>
    </>
  );
}

export default function PortfolioCheckCard() {
  const { data, isLoading, error } = usePortfolioCheck();

  if (isLoading) {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 2 }}>
        <CircularProgress size={18} />
        <Typography variant="body2" color="text.secondary">Checking your portfolio…</Typography>
      </Box>
    );
  }
  if (error || !data) {
    return <Alert severity="error">Couldn&apos;t run the Portfolio Check.</Alert>;
  }
  if (data.holdings.length === 0) {
    return (
      <Alert severity="info">
        No open lots yet. Add your holdings on the Portfolio page and the check will cover them.
      </Alert>
    );
  }

  const total = data.industries.length;

  return (
    <Box data-testid="portfolio-check-card">
      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mb: 1 }} alignItems="center">
        <Typography variant="subtitle2" sx={{ mr: 1 }}>
          Your portfolio · {formatCurrency(data.total_value)}
        </Typography>
        {data.action_counts.map(({ action, count }) => (
          <Chip key={action} size="small" color={actionColor(action)} label={`${count} ${action}`} />
        ))}
      </Stack>
      {data.unpriced.length > 0 && (
        <Alert severity="warning" sx={{ mb: 1 }}>
          No current price for {data.unpriced.join(', ')}; counted as zero value.
        </Alert>
      )}
      <Paper variant="outlined" sx={{ overflowX: 'auto' }}>
        <Table size="small" aria-label="Portfolio Check holdings">
          <TableHead>
            <TableRow>
              <TableCell>Action</TableCell>
              <TableCell>Stock</TableCell>
              <TableCell>Industry</TableCell>
              <TableCell align="right">Weight</TableCell>
              <TableCell align="right">vs peers, 12 mo</TableCell>
              <TableCell align="right">Value</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {data.holdings.map((h) => (
              <HoldingRow key={h.ticker} h={h} total={total} />
            ))}
          </TableBody>
        </Table>
      </Paper>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
        Click a row for the reasons and tax note. Industry signals as of {data.market_state.industries_asof};
        research output, not financial advice.
      </Typography>
    </Box>
  );
}
