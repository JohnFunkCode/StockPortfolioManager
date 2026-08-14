/**
 * The `/watchlist` page — the replacement for the nightly HTML report
 * (issue #147 Part C).
 *
 * One request feeds the whole table (`GET /api/watchlist/fundamentals`, six
 * queries and zero network calls server-side), so everything here is rendering
 * and filtering. Two rules the report taught us, kept visible:
 *
 * - **Returns arrive as percent, not fractions** (`quantcore/analytics/returns.py`
 *   — "dates and closes in, Decimal percent out"), so the columns use
 *   `formatPercentRaw`. Running them through `formatPercent` would multiply by
 *   100 a second time.
 * - **A market cap is only comparable in one currency.** The native column is
 *   deliberately unsortable and the USD column is the sortable one, with its
 *   nulls forced to the bottom in both directions — every non-USD name is null
 *   there until an FX source lands (issue #185), and a null that floats to the
 *   top of a "biggest first" sort reads as an answer.
 */
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  FormControl,
  IconButton,
  InputLabel,
  MenuItem,
  OutlinedInput,
  Paper,
  Select,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import type { SelectChangeEvent } from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import LocalOfferIcon from '@mui/icons-material/LocalOffer';
import {
  DataGrid,
  GridColDef,
  GridRenderCellParams,
  GridSortModel,
} from '@mui/x-data-grid';
import type { GridComparatorFn, GridSortDirection } from '@mui/x-data-grid';

import AddSecurityDialog from '../securities/AddSecurityDialog';
import LoadingSpinner from '../common/LoadingSpinner';
import ErrorAlert from '../common/ErrorAlert';
import { useWatchlistFundamentals, useSetWatchlistTags } from '../../hooks/useWatchlist';
import { useRemoveFromWatchlist } from '../../hooks/useSecurities';
import type { WatchlistFundamentalsRow } from '../../api/watchlist';
import {
  formatCurrency,
  formatDate,
  formatMarketCap,
  formatPercentRaw,
} from '../../utils/formatting';

const GREEN = '#10b981';
const RED = '#ef4444';
const MUTED = '#6b7280';

/** Colour a return, leaving "not known" grey rather than calling it flat. */
function ReturnCell({ value }: { value: number | null | undefined }) {
  if (value == null) {
    return <Typography variant="body2" sx={{ color: '#4b5563' }}>—</Typography>;
  }
  return (
    <Typography variant="body2" sx={{ color: value >= 0 ? GREEN : RED, fontWeight: 500 }}>
      {formatPercentRaw(value, 1)}
    </Typography>
  );
}

/** Composite score descending — the order the nightly report ranked by, so the
 * page opens on the same top of the list.
 *
 * One item, not two. The Community DataGrid forces
 * `disableMultipleColumnsSorting`, and `sanitizeSortModel` truncates a longer
 * model to `[model[0]]`: a second entry for the tie-break would read as applied
 * and do nothing. The tie-break lives in `scoreThenReturn` instead. */
const DEFAULT_SORT_MODEL: GridSortModel = [{ field: 'composite_score', sort: 'desc' }];

const LABEL_COLORS: Record<string, string> = {
  strong: GREEN,
  improving: '#34d399',
  mixed: '#9ca3af',
  weak: RED,
};

/** Nulls at the bottom whichever way the column is sorted.
 *
 * DataGrid's own number comparator treats null as smaller than everything,
 * which parks the unpriced names at the top of a descending "biggest cap"
 * sort. `getSortComparator` is the hook that fixes it — but note DataGrid uses
 * what this returns *verbatim*, it does not flip the result for `desc`. So the
 * comparator applies the direction itself (`sign`) to the numbers, and pins the
 * nulls to the bottom unconditionally, outside the sign. */
function nullsLast(direction: GridSortDirection) {
  const sign = direction === 'desc' ? -1 : 1;
  return (a: number | null, b: number | null) => {
    if (a == null && b == null) return 0;
    if (a == null) return 1;
    if (b == null) return -1;
    return sign * (a - b);
  };
}

/** Score first, 30-day return to break the tie — the report's ranking, and the
 * reason `DEFAULT_SORT_MODEL` is a single item. Reaching the other column means
 * reading it off the row (`api.getRow`), since the comparator is only handed
 * its own column's two values. */
function scoreThenReturn(direction: GridSortDirection): GridComparatorFn<number | null> {
  const compare = nullsLast(direction);
  return (a, b, paramsA, paramsB) => {
    const primary = compare(a, b);
    if (primary !== 0) return primary;
    const rowA = paramsA.api.getRow(paramsA.id) as WatchlistFundamentalsRow | null;
    const rowB = paramsB.api.getRow(paramsB.id) as WatchlistFundamentalsRow | null;
    return compare(rowA?.return_30d ?? null, rowB?.return_30d ?? null);
  };
}

/** localStorage is user-writable, and `JSON.parse` succeeding says nothing
 * about the shape it returned. A stored `null` or `{}` parses fine and then
 * takes the page down on the first `.length` — so validate, don't just catch. */
function readTagFilter(): string[] {
  try {
    const parsed = JSON.parse(localStorage.getItem('watchlist-tag-filter') ?? 'null');
    return Array.isArray(parsed) && parsed.every((t) => typeof t === 'string') ? parsed : [];
  } catch {
    return [];
  }
}

function readSortModel(): GridSortModel {
  try {
    const parsed = JSON.parse(localStorage.getItem('watchlist-sort-model') ?? 'null');
    const usable =
      Array.isArray(parsed) &&
      parsed.length > 0 &&
      parsed.every(
        (item) =>
          item != null &&
          typeof item.field === 'string' &&
          (item.sort === 'asc' || item.sort === 'desc' || item.sort == null),
      );
    return usable ? (parsed as GridSortModel) : DEFAULT_SORT_MODEL;
  } catch {
    return DEFAULT_SORT_MODEL;
  }
}

export default function WatchlistPage() {
  const navigate = useNavigate();
  const { data, isLoading, error } = useWatchlistFundamentals();
  const setTags = useSetWatchlistTags();
  const removeFromWatchlist = useRemoveFromWatchlist();

  const [search, setSearch] = useState('');
  const [addOpen, setAddOpen] = useState(false);
  const [removeTarget, setRemoveTarget] = useState<string | null>(null);
  const [tagTarget, setTagTarget] = useState<WatchlistFundamentalsRow | null>(null);
  const [tagChips, setTagChips] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState('');

  const [tagFilter, setTagFilter] = useState<string[]>(readTagFilter);
  const [sortModel, setSortModel] = useState<GridSortModel>(readSortModel);

  const setTagFilterPersisted = (tags: string[] | ((prev: string[]) => string[])) => {
    setTagFilter((prev) => {
      const next = typeof tags === 'function' ? tags(prev) : tags;
      localStorage.setItem('watchlist-tag-filter', JSON.stringify(next));
      return next;
    });
  };

  const rows = useMemo<WatchlistFundamentalsRow[]>(() => data?.rows ?? [], [data]);

  const allTags = useMemo<string[]>(() => {
    const set = new Set<string>();
    rows.forEach((r) => (r.tags ?? []).forEach((t) => set.add(t)));
    return Array.from(set).sort();
  }, [rows]);

  const filtered = useMemo<WatchlistFundamentalsRow[]>(
    () =>
      rows.filter((r) => {
        if (tagFilter.length > 0 && !tagFilter.some((t) => (r.tags ?? []).includes(t))) {
          return false;
        }
        if (search) {
          const q = search.toLowerCase();
          if (!r.symbol.toLowerCase().includes(q) && !(r.name ?? '').toLowerCase().includes(q)) {
            return false;
          }
        }
        return true;
      }),
    [rows, tagFilter, search],
  );

  const openTagEditor = (row: WatchlistFundamentalsRow) => {
    setTagTarget(row);
    setTagChips([...(row.tags ?? [])]);
    setTagInput('');
  };

  const commitTagInput = () => {
    const tag = tagInput.trim().replace(/,$/, '');
    if (tag && !tagChips.includes(tag)) setTagChips((c) => [...c, tag]);
    setTagInput('');
  };

  const handleTagKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      commitTagInput();
    }
  };

  const saveTags = () => {
    if (!tagTarget) return;
    // The full chip set the dialog is showing, not a delta — the server
    // replaces, which is the only way a tag can ever be removed.
    setTags.mutate(
      { ticker: tagTarget.symbol, tags: tagChips },
      { onSuccess: () => setTagTarget(null) },
    );
  };

  const confirmRemove = () => {
    if (!removeTarget) return;
    removeFromWatchlist.mutate(removeTarget, { onSuccess: () => setRemoveTarget(null) });
  };

  const columns: GridColDef<WatchlistFundamentalsRow>[] = [
    {
      field: 'symbol',
      headerName: 'Symbol',
      width: 100,
      renderCell: (p: GridRenderCellParams<WatchlistFundamentalsRow, string>) => (
        <Typography
          variant="body2"
          sx={{ cursor: 'pointer', color: 'secondary.main', fontWeight: 600, lineHeight: '52px' }}
          onClick={() => navigate(`/securities/${p.value}`)}
        >
          {p.value}
        </Typography>
      ),
    },
    { field: 'name', headerName: 'Name', flex: 1, minWidth: 160 },
    {
      field: 'price',
      headerName: 'Price',
      width: 100,
      type: 'number',
      renderCell: (p: GridRenderCellParams<WatchlistFundamentalsRow, number | null>) => (
        <Typography variant="body2">
          {p.value == null ? '—' : formatCurrency(p.value, p.row.currency)}
        </Typography>
      ),
    },
    {
      field: 'return_5d',
      headerName: '5d',
      width: 70,
      type: 'number',
      renderCell: (p: GridRenderCellParams<WatchlistFundamentalsRow, number | null>) => (
        <ReturnCell value={p.value} />
      ),
    },
    {
      field: 'return_30d',
      headerName: '30d',
      width: 70,
      type: 'number',
      renderCell: (p: GridRenderCellParams<WatchlistFundamentalsRow, number | null>) => (
        <ReturnCell value={p.value} />
      ),
    },
    {
      field: 'return_60d',
      headerName: '60d',
      width: 70,
      type: 'number',
      renderCell: (p: GridRenderCellParams<WatchlistFundamentalsRow, number | null>) => (
        <ReturnCell value={p.value} />
      ),
    },
    {
      field: 'return_ytd',
      headerName: 'YTD',
      width: 70,
      type: 'number',
      renderCell: (p: GridRenderCellParams<WatchlistFundamentalsRow, number | null>) => (
        <ReturnCell value={p.value} />
      ),
    },
    {
      field: 'return_1y',
      headerName: '1y',
      width: 70,
      type: 'number',
      renderCell: (p: GridRenderCellParams<WatchlistFundamentalsRow, number | null>) => (
        <ReturnCell value={p.value} />
      ),
    },
    {
      field: 'composite_score',
      headerName: 'Score',
      width: 92,
      type: 'number',
      getSortComparator: scoreThenReturn,
      renderCell: (p: GridRenderCellParams<WatchlistFundamentalsRow, number | null>) => {
        if (p.value == null) {
          return (
            <Tooltip title="No cached fundamentals for this symbol yet">
              <Typography variant="body2" sx={{ color: '#4b5563' }}>—</Typography>
            </Tooltip>
          );
        }
        const color = LABEL_COLORS[p.row.fundamental_label ?? 'mixed'] ?? '#9ca3af';
        return (
          <Tooltip title={p.row.fundamental_label ?? ''}>
            <Typography variant="body2" sx={{ color, fontWeight: 600 }}>
              {p.value.toFixed(0)}
            </Typography>
          </Tooltip>
        );
      },
    },
    { field: 'sector', headerName: 'Sector', width: 120 },
    {
      field: 'market_cap_usd',
      headerName: 'Cap (USD)',
      width: 110,
      type: 'number',
      getSortComparator: nullsLast,
      renderCell: (p: GridRenderCellParams<WatchlistFundamentalsRow, number | null>) =>
        p.value == null ? (
          <Tooltip title="No USD conversion for a non-dollar listing yet (issue #185)">
            <Typography variant="body2" sx={{ color: '#4b5563' }}>—</Typography>
          </Tooltip>
        ) : (
          <Typography variant="body2">{formatMarketCap(p.value, 'USD')}</Typography>
        ),
    },
    {
      field: 'market_cap',
      headerName: 'Cap (native)',
      width: 112,
      // Deliberately not sortable: the values are in different currencies, so
      // ordering them ranks exchange rates rather than companies.
      sortable: false,
      renderCell: (p: GridRenderCellParams<WatchlistFundamentalsRow, number | null>) => (
        <Typography variant="body2" sx={{ color: '#d1d5db' }}>
          {formatMarketCap(p.value, p.row.market_cap_currency ?? p.row.currency)}
        </Typography>
      ),
    },
    {
      field: 'fundamentals_age_hours',
      headerName: 'Cached',
      width: 86,
      type: 'number',
      getSortComparator: nullsLast,
      renderCell: (p: GridRenderCellParams<WatchlistFundamentalsRow, number | null>) => {
        if (p.value == null) {
          return <Typography variant="body2" sx={{ color: '#4b5563' }}>—</Typography>;
        }
        const stale = p.row.fundamentals_stale === true;
        return (
          <Tooltip title={p.row.fundamentals_cached_at ?? ''}>
            <Chip
              size="small"
              label={`${p.value.toFixed(0)}h`}
              sx={{
                fontSize: 11,
                height: 20,
                bgcolor: stale ? '#3a2e1e' : '#1f2937',
                color: stale ? '#f59e0b' : '#9ca3af',
                border: `1px solid ${stale ? '#f59e0b' : '#4b5563'}`,
              }}
            />
          </Tooltip>
        );
      },
    },
    {
      field: 'tags',
      headerName: 'Tags',
      flex: 1,
      minWidth: 150,
      sortable: false,
      renderCell: (p: GridRenderCellParams<WatchlistFundamentalsRow, string[]>) => (
        <Stack
          direction="row"
          spacing={0.5}
          flexWrap="wrap"
          useFlexGap
          alignItems="center"
          sx={{ height: '100%' }}
          onClick={(e) => e.stopPropagation()}
        >
          {(p.value ?? []).slice(0, 4).map((t) => (
            <Chip
              key={t}
              label={t}
              size="small"
              variant={tagFilter.includes(t) ? 'filled' : 'outlined'}
              color={tagFilter.includes(t) ? 'secondary' : 'default'}
              clickable
              onClick={() =>
                setTagFilterPersisted((prev) =>
                  prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t],
                )
              }
              sx={{ fontSize: 11 }}
            />
          ))}
          {(p.value ?? []).length > 4 && (
            <Tooltip title={(p.value ?? []).slice(4).join(', ')}>
              <Chip label={`+${(p.value ?? []).length - 4}`} size="small" />
            </Tooltip>
          )}
        </Stack>
      ),
    },
    {
      field: '_actions',
      headerName: '',
      width: 96,
      sortable: false,
      renderCell: (p: GridRenderCellParams<WatchlistFundamentalsRow>) => (
        <Stack direction="row" spacing={0.5} onClick={(e) => e.stopPropagation()}>
          <Tooltip title="Edit tags">
            <IconButton
              size="small"
              aria-label={`Edit tags for ${p.row.symbol}`}
              onClick={() => openTagEditor(p.row)}
            >
              <LocalOfferIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title="Remove from watchlist">
            <IconButton
              size="small"
              aria-label={`Remove ${p.row.symbol} from watchlist`}
              onClick={() => setRemoveTarget(p.row.symbol)}
            >
              <DeleteOutlineIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Stack>
      ),
    },
  ];

  if (isLoading) return <LoadingSpinner />;
  if (error) return <ErrorAlert message={(error as Error).message} />;

  return (
    <Box>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
        <Stack direction="row" spacing={1.5} alignItems="baseline" flexWrap="wrap" useFlexGap>
          <Typography variant="h4">Watchlist</Typography>
          <Chip size="small" label={`${data?.count ?? 0} securities`} sx={{ fontSize: 11 }} />
          {data?.as_of && (
            <Chip
              size="small"
              label={`prices ${formatDate(data.as_of)}`}
              sx={{ fontSize: 11, color: '#9ca3af' }}
              variant="outlined"
            />
          )}
          {(data?.stale_count ?? 0) > 0 && (
            <Tooltip title="Cached fundamentals older than their TTL — shown, not dropped">
              <Chip
                size="small"
                label={`${data?.stale_count} stale`}
                sx={{ fontSize: 11, bgcolor: '#3a2e1e', color: '#f59e0b', border: '1px solid #f59e0b' }}
              />
            </Tooltip>
          )}
          {(data?.unscored_count ?? 0) > 0 && (
            <Tooltip title="No fundamentals cached yet — the nightly warmer converges over a few nights">
              <Chip
                size="small"
                label={`${data?.unscored_count} unscored`}
                variant="outlined"
                sx={{ fontSize: 11, color: MUTED }}
              />
            </Tooltip>
          )}
        </Stack>
        <Button variant="contained" startIcon={<AddIcon />} onClick={() => setAddOpen(true)}>
          Add Security
        </Button>
      </Stack>

      <Paper sx={{ p: 2, mb: 2 }}>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} alignItems="center">
          <TextField
            size="small"
            label="Search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            sx={{ minWidth: 180 }}
          />

          <FormControl size="small" sx={{ minWidth: 220 }}>
            <InputLabel>Tags</InputLabel>
            <Select
              multiple
              value={tagFilter}
              onChange={(e: SelectChangeEvent<string[]>) =>
                setTagFilterPersisted(
                  typeof e.target.value === 'string' ? e.target.value.split(',') : e.target.value,
                )
              }
              input={<OutlinedInput label="Tags" />}
              renderValue={(selected) => (
                <Stack direction="row" spacing={0.5} flexWrap="wrap">
                  {(selected as string[]).map((t) => (
                    <Chip key={t} label={t} size="small" />
                  ))}
                </Stack>
              )}
            >
              {allTags.map((t) => (
                <MenuItem key={t} value={t}>{t}</MenuItem>
              ))}
            </Select>
          </FormControl>

          {tagFilter.length > 0 && (
            <Button
              size="small"
              variant="outlined"
              color="secondary"
              onClick={() => setTagFilterPersisted([])}
            >
              Clear Tags ({tagFilter.length})
            </Button>
          )}

          <Typography variant="body2" color="text.secondary" sx={{ ml: 'auto' }}>
            {filtered.length} of {rows.length} securities
          </Typography>
        </Stack>
      </Paper>

      <DataGrid
        rows={filtered}
        columns={columns}
        getRowId={(row) => row.symbol}
        autoHeight
        rowHeight={52}
        disableRowSelectionOnClick
        pageSizeOptions={[25, 50, 100]}
        initialState={{ pagination: { paginationModel: { pageSize: 25 } } }}
        sortModel={sortModel}
        onSortModelChange={(model) => {
          setSortModel(model);
          localStorage.setItem('watchlist-sort-model', JSON.stringify(model));
        }}
        onRowClick={(params) => navigate(`/securities/${params.row.symbol}`)}
        sx={{ cursor: 'pointer' }}
      />

      <AddSecurityDialog open={addOpen} onClose={() => setAddOpen(false)} />

      {/* Tag editor — chips in, chips out; the whole set is sent on save. */}
      <Dialog open={tagTarget !== null} onClose={() => setTagTarget(null)} maxWidth="xs" fullWidth>
        <DialogTitle>Tags — {tagTarget?.symbol}</DialogTitle>
        <DialogContent>
          <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap sx={{ mb: 2, mt: 1 }}>
            {tagChips.map((t) => (
              <Chip
                key={t}
                label={t}
                size="small"
                onDelete={() => setTagChips((c) => c.filter((x) => x !== t))}
              />
            ))}
            {tagChips.length === 0 && (
              <Typography variant="caption" sx={{ color: MUTED }}>
                No tags — saving now clears every tag on this symbol.
              </Typography>
            )}
          </Stack>
          <TextField
            size="small"
            fullWidth
            label="Add tag"
            placeholder="Enter or comma to add"
            value={tagInput}
            onChange={(e) => setTagInput(e.target.value)}
            onKeyDown={handleTagKey}
            onBlur={commitTagInput}
          />
          {setTags.error && (
            <Typography variant="caption" sx={{ color: RED, display: 'block', mt: 1 }}>
              {(setTags.error as Error).message}
            </Typography>
          )}
        </DialogContent>
        <DialogActions>
          <Button size="small" onClick={() => setTagTarget(null)}>Cancel</Button>
          <Button size="small" variant="contained" onClick={saveTags} disabled={setTags.isPending}>
            {setTags.isPending ? 'Saving…' : 'Save'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Removal — the same wording as the detail page, because it is the same
          shared list and the consequence lands on everyone. */}
      <Dialog open={removeTarget !== null} onClose={() => setRemoveTarget(null)} maxWidth="xs" fullWidth>
        <DialogTitle>Remove from Watchlist</DialogTitle>
        <DialogContent>
          <DialogContentText>
            Remove <strong>{removeTarget}</strong> from the watchlist? The watchlist is shared, so
            this removes it for everyone. Daily reports and options-chain capture will stop
            covering it.
          </DialogContentText>
          {removeFromWatchlist.error && (
            <Typography variant="caption" sx={{ color: RED, display: 'block', mt: 1 }}>
              {(removeFromWatchlist.error as Error).message}
            </Typography>
          )}
        </DialogContent>
        <DialogActions>
          <Button size="small" onClick={() => setRemoveTarget(null)}>Cancel</Button>
          <Button
            size="small"
            variant="contained"
            color="warning"
            onClick={confirmRemove}
            disabled={removeFromWatchlist.isPending}
          >
            {removeFromWatchlist.isPending ? 'Removing…' : 'Remove'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
