import { Fragment, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Collapse,
  IconButton,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TableSortLabel,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import RefreshIcon from '@mui/icons-material/Refresh';
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown';
import KeyboardArrowRightIcon from '@mui/icons-material/KeyboardArrowRight';
import { useSymbolRows } from '../../hooks/usePortfolio';
import LoadingSpinner from '../common/LoadingSpinner';
import ErrorAlert from '../common/ErrorAlert';
import EmptyPortfolio from './EmptyPortfolio';
import PortfolioSummary from './PortfolioSummary';
import LotRow from './LotRow';
import AddLotDialog from './AddLotDialog';
import { formatCurrency, formatDollarsPerDay, formatPercentRaw } from '../../utils/formatting';
import type { SymbolRow } from '../../api/portfolioTypes';

type SortKey =
  | 'symbol'
  | 'total_investment'
  | 'total_current_value'
  | 'total_gain_loss'
  | 'total_gain_loss_pct'
  | 'total_dollars_per_day'
  | 'return_7d'
  | 'return_30d'
  | 'return_90d';

const COLUMNS: Array<{ key: SortKey; label: string; align?: 'right' }> = [
  { key: 'symbol', label: 'Symbol' },
  { key: 'total_investment', label: 'Investment', align: 'right' },
  { key: 'total_current_value', label: 'Current Value', align: 'right' },
  { key: 'total_gain_loss', label: 'Gain/Loss $', align: 'right' },
  { key: 'total_gain_loss_pct', label: 'Gain/Loss %', align: 'right' },
  { key: 'total_dollars_per_day', label: '$/Day', align: 'right' },
  { key: 'return_7d', label: '7d', align: 'right' },
  { key: 'return_30d', label: '30d', align: 'right' },
  { key: 'return_90d', label: '90d', align: 'right' },
];

function gainColor(value: number | null): string | undefined {
  if (value == null) return undefined;
  return value >= 0 ? '#10b981' : '#ef4444';
}

const HEDGE_COLORS: Record<string, { bg: string; color: string }> = {
  buy_on_rally: { bg: '#1e3a2f', color: '#10b981' },
};

export default function PortfolioPage() {
  const navigate = useNavigate();
  const { data, isLoading, error, refetch, isFetching, dataUpdatedAt } = useSymbolRows();

  const [sortKey, setSortKey] = useState<SortKey>('symbol');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [addOpen, setAddOpen] = useState(false);

  const rows = data?.symbols ?? [];

  const sorted = useMemo<SymbolRow[]>(() => {
    const copy = [...rows];
    copy.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === 'string' || typeof bv === 'string') {
        return String(av).localeCompare(String(bv));
      }
      return (av as number) - (bv as number);
    });
    if (sortDir === 'desc') copy.reverse();
    return copy;
  }, [rows, sortKey, sortDir]);

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('asc');
    }
  };

  const toggleExpanded = (symbol: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(symbol)) next.delete(symbol);
      else next.add(symbol);
      return next;
    });
  };

  if (isLoading) return <LoadingSpinner />;
  if (error) return <ErrorAlert message={(error as Error).message} />;

  return (
    <Box>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
        <Typography variant="h4">Portfolio</Typography>
        <Stack direction="row" spacing={1} alignItems="center">
          {dataUpdatedAt > 0 && (
            <Typography variant="caption" color="text.secondary">
              as of {new Date(dataUpdatedAt).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}
            </Typography>
          )}
          <Button
            size="small"
            startIcon={isFetching ? <CircularProgress size={14} /> : <RefreshIcon />}
            onClick={() => refetch()}
            disabled={isFetching}
          >
            Refresh
          </Button>
          <Button variant="contained" startIcon={<AddIcon />} onClick={() => setAddOpen(true)}>
            Add Lot
          </Button>
        </Stack>
      </Stack>

      {rows.length === 0 ? (
        <EmptyPortfolio />
      ) : (
        <>
          {data?.totals && <PortfolioSummary totals={data.totals} />}

          <Paper sx={{ overflowX: 'auto' }}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell />
                  {COLUMNS.map((col) => (
                    <TableCell key={col.key} align={col.align}>
                      <TableSortLabel
                        active={sortKey === col.key}
                        direction={sortKey === col.key ? sortDir : 'asc'}
                        onClick={() => handleSort(col.key)}
                      >
                        {col.label}
                      </TableSortLabel>
                    </TableCell>
                  ))}
                  <TableCell>MM Hedge Bias</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {sorted.map((row) => {
                  const isOpen = expanded.has(row.symbol);
                  const hedge = row.mm_hedge_bias
                    ? (HEDGE_COLORS[row.mm_hedge_bias] ?? { bg: '#3a1e1e', color: '#ef4444' })
                    : null;
                  return (
                    <Fragment key={row.symbol}>
                      <TableRow hover sx={{ cursor: 'pointer' }}>
                        <TableCell>
                          <IconButton size="small" onClick={() => toggleExpanded(row.symbol)}>
                            {isOpen ? <KeyboardArrowDownIcon fontSize="small" /> : <KeyboardArrowRightIcon fontSize="small" />}
                          </IconButton>
                        </TableCell>
                        <TableCell onClick={() => navigate(`/securities/${row.symbol}`)}>
                          <Typography variant="body2" sx={{ fontWeight: 600, color: 'secondary.main' }}>
                            {row.symbol}
                          </Typography>
                        </TableCell>
                        <TableCell align="right">{formatCurrency(row.total_investment)}</TableCell>
                        <TableCell align="right">{formatCurrency(row.total_current_value)}</TableCell>
                        <TableCell align="right" sx={{ color: gainColor(row.total_gain_loss) }}>
                          {formatCurrency(row.total_gain_loss)}
                        </TableCell>
                        <TableCell align="right" sx={{ color: gainColor(row.total_gain_loss_pct) }}>
                          {formatPercentRaw(row.total_gain_loss_pct)}
                        </TableCell>
                        <TableCell align="right">{formatDollarsPerDay(row.total_dollars_per_day)}</TableCell>
                        <TableCell align="right" sx={{ color: gainColor(row.return_7d) }}>
                          {formatPercentRaw(row.return_7d)}
                        </TableCell>
                        <TableCell align="right" sx={{ color: gainColor(row.return_30d) }}>
                          {formatPercentRaw(row.return_30d)}
                        </TableCell>
                        <TableCell align="right" sx={{ color: gainColor(row.return_90d) }}>
                          {formatPercentRaw(row.return_90d)}
                        </TableCell>
                        <TableCell>
                          {hedge ? (
                            <Chip
                              size="small"
                              label={row.mm_hedge_bias}
                              sx={{ bgcolor: hedge.bg, color: hedge.color }}
                            />
                          ) : (
                            '—'
                          )}
                        </TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell colSpan={COLUMNS.length + 2} sx={{ p: 0, borderBottom: isOpen ? undefined : 'none' }}>
                          <Collapse in={isOpen} unmountOnExit>
                            <Box sx={{ pl: 6, pr: 2, py: 1 }}>
                              <Table size="small">
                                <TableHead>
                                  <TableRow>
                                    <TableCell />
                                    <TableCell>Price Paid</TableCell>
                                    <TableCell>Shares</TableCell>
                                    <TableCell>Gain/Loss %</TableCell>
                                    <TableCell>Gain/Loss $</TableCell>
                                    <TableCell>Days Held</TableCell>
                                    <TableCell>$/Day</TableCell>
                                    <TableCell align="right">Actions</TableCell>
                                  </TableRow>
                                </TableHead>
                                <TableBody>
                                  {row.lots.map((lot) => (
                                    <LotRow key={lot.lot_id} lot={lot} />
                                  ))}
                                </TableBody>
                              </Table>
                            </Box>
                          </Collapse>
                        </TableCell>
                      </TableRow>
                    </Fragment>
                  );
                })}
              </TableBody>
            </Table>
          </Paper>
        </>
      )}

      <AddLotDialog open={addOpen} onClose={() => setAddOpen(false)} />
    </Box>
  );
}
