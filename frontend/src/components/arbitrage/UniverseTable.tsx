/**
 * UniverseTable — every curated link in arb_universe.yaml, one row per pair.
 *
 * The scan says how a pair scores; this says what is configured and why. A row
 * expands to the curator's rationale and, for NAV vehicles, the hand-maintained
 * capital figures (they are what goes stale). Nothing here is computed in the
 * browser: holdings age, staleness and hedge availability arrive from
 * ArbitrageService.get_universe.
 */
import { Fragment, useState } from 'react';
import {
  Box,
  Chip,
  Collapse,
  IconButton,
  Link,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown';
import KeyboardArrowRightIcon from '@mui/icons-material/KeyboardArrowRight';
import type { UniverseEntry } from '../../api/arbitrage';

const COMPACT = new Intl.NumberFormat('en-US', {
  notation: 'compact',
  maximumFractionDigits: 1,
});

function compact(value: number | null): string {
  return value == null ? '—' : COMPACT.format(value);
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <Box>
      <Typography variant="caption" sx={{ color: 'var(--qc-muted)', display: 'block' }}>
        {label}
      </Typography>
      <Typography variant="body2">{value}</Typography>
    </Box>
  );
}

function Details({
  entry,
  onOpenWorkup,
}: {
  entry: UniverseEntry;
  onOpenWorkup?: (security: string) => void;
}) {
  return (
    <Stack spacing={1.5} sx={{ py: 1.5, pl: 6, pr: 2 }}>
      <Box>
        <Typography variant="caption" sx={{ color: 'var(--qc-muted)', display: 'block' }}>
          Why this link exists
        </Typography>
        <Typography variant="body2" sx={{ maxWidth: 900 }}>
          {entry.notes ?? 'No rationale recorded for this pair.'}
        </Typography>
      </Box>
      {entry.kind === 'nav_vehicle' && (
        <Stack
          direction="row"
          spacing={4}
          useFlexGap
          flexWrap="wrap"
          data-testid={`universe-capital-${entry.security}`}
        >
          <Figure
            label={`Units of ${entry.underlying} held`}
            value={
              entry.holdings_units == null
                ? '—'
                : entry.holdings_units.toLocaleString('en-US')
            }
          />
          <Figure
            label="Senior claims"
            value={entry.senior_claims_usd == null ? '—' : `$${compact(entry.senior_claims_usd)}`}
          />
          <Figure label="Diluted shares" value={compact(entry.diluted_shares)} />
          <Figure label="Source" value={entry.source ?? '—'} />
        </Stack>
      )}
      {onOpenWorkup && (
        <Box>
          <Link
            component="button"
            type="button"
            variant="body2"
            onClick={() => onOpenWorkup(entry.security)}
          >
            Open workup →
          </Link>
        </Box>
      )}
    </Stack>
  );
}

interface Props {
  entries: UniverseEntry[];
  /** Omitted in chat, where the card has no pair detail to open. */
  onOpenWorkup?: (security: string) => void;
}

export default function UniverseTable({ entries, onOpenWorkup }: Props) {
  const [open, setOpen] = useState<string | null>(null);

  if (entries.length === 0) {
    return (
      <Typography variant="body2" color="text.secondary" data-testid="universe-table">
        No curated pairs are configured.
      </Typography>
    );
  }

  return (
    <TableContainer data-testid="universe-table">
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell sx={{ width: 48 }} />
            <TableCell>Symbol</TableCell>
            <TableCell>Family</TableCell>
            <TableCell>Underlying</TableCell>
            <TableCell>Hedge</TableCell>
            <TableCell>Convergence</TableCell>
            <TableCell>Holdings as of</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {entries.map((e) => {
            const expanded = open === e.security;
            return (
              <Fragment key={e.security}>
                <TableRow
                  hover
                  onClick={() => setOpen(expanded ? null : e.security)}
                  sx={{ cursor: 'pointer', '& > td': { borderBottom: 'unset' } }}
                >
                  <TableCell>
                    <IconButton
                      size="small"
                      aria-expanded={expanded}
                      aria-label={`${expanded ? 'Hide' : 'Show'} details for ${e.security}`}
                    >
                      {expanded ? (
                        <KeyboardArrowDownIcon fontSize="small" />
                      ) : (
                        <KeyboardArrowRightIcon fontSize="small" />
                      )}
                    </IconButton>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2" fontWeight={600}>
                      {e.security}
                    </Typography>
                    <Typography variant="caption" sx={{ color: 'var(--qc-muted)' }}>
                      {e.name}
                    </Typography>
                  </TableCell>
                  <TableCell>{e.kind.replace(/_/g, ' ')}</TableCell>
                  <TableCell>{e.underlying}</TableCell>
                  <TableCell>
                    {e.hedge_available ? (
                      e.hedge_instrument
                    ) : (
                      <Chip size="small" label="futures only" color="warning" variant="outlined" />
                    )}
                  </TableCell>
                  <TableCell>{e.convergence_mechanism.replace(/_/g, ' ')}</TableCell>
                  <TableCell>
                    {e.holdings_as_of ? (
                      <Stack direction="row" spacing={1} alignItems="center">
                        <Box component="span" sx={{ whiteSpace: 'nowrap' }}>
                          {e.holdings_as_of}
                        </Box>
                        {e.holdings_stale && e.holdings_age_days != null && (
                          <Chip
                            size="small"
                            color="warning"
                            variant="outlined"
                            label={`${e.holdings_age_days} days old`}
                          />
                        )}
                      </Stack>
                    ) : (
                      '—'
                    )}
                  </TableCell>
                </TableRow>
                <TableRow>
                  <TableCell colSpan={7} sx={{ py: 0, borderColor: 'var(--qc-line)' }}>
                    <Collapse in={expanded} timeout="auto" unmountOnExit>
                      <Details entry={e} onOpenWorkup={onOpenWorkup} />
                    </Collapse>
                  </TableCell>
                </TableRow>
              </Fragment>
            );
          })}
        </TableBody>
      </Table>
    </TableContainer>
  );
}
