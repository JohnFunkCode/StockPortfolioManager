import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import AddSecurityDialog from './AddSecurityDialog';
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Collapse,
  FormControl,
  InputLabel,
  MenuItem,
  OutlinedInput,
  Paper,
  Select,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import FilterListIcon from '@mui/icons-material/FilterList';
import RefreshIcon from '@mui/icons-material/Refresh';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import { DataGrid, GridColDef, GridRenderCellParams, GridSortModel } from '@mui/x-data-grid';
import AccountBalanceWalletIcon from '@mui/icons-material/AccountBalanceWallet';
import VisibilityIcon from '@mui/icons-material/Visibility';
import type { SelectChangeEvent } from '@mui/material';
import { useSecurities, useScreener, useRefreshOptionsSnapshots } from '../../hooks/useSecurities';
import LoadingSpinner from '../common/LoadingSpinner';
import ErrorAlert from '../common/ErrorAlert';
import type { Security, ScreenerResult } from '../../api/securitiesTypes';

type SourceFilter = 'all' | 'portfolio' | 'watchlist' | 'both';

const SCREENER_PRESETS = [
  { label: 'Oversold (RSI < 30)',    params: { rsi_max: '30' } },
  { label: 'Overbought (RSI > 70)',  params: { rsi_min: '70' } },
  { label: 'Near BB Lower',          params: { near_bb_lower: '1' } },
  { label: 'Near BB Upper',          params: { near_bb_upper: '1' } },
  { label: 'Above MA200',            params: { above_ma200: '1' } },
  { label: 'Below MA200',            params: { below_ma200: '1' } },
  { label: 'MACD Bullish',           params: { macd_bullish: '1' } },
  { label: 'MACD Bearish',           params: { macd_bearish: '1' } },
] as const;

const SOURCE_COLORS: Record<string, 'primary' | 'success' | 'secondary'> = {
  portfolio: 'primary',
  watchlist: 'success',
  both: 'secondary',
};

export default function SecuritiesPage() {
  const navigate = useNavigate();
  const { data, isLoading, error } = useSecurities();

  const [sourceFilter, setSourceFilter] = useState<SourceFilter>('all');
  const [tagFilter, setTagFilter] = useState<string[]>(() => {
    try {
      const saved = localStorage.getItem('securities-tag-filter');
      return saved ? JSON.parse(saved) : [];
    } catch {
      return [];
    }
  });
  const [search, setSearch] = useState('');
  const [screenerOpen, setScreenerOpen] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [screenerParams, setScreenerParams] = useState<Record<string, string>>({});
  const [activePreset, setActivePreset] = useState<string | null>(null);

  const [sortModel, setSortModel] = useState<GridSortModel>(() => {
    try {
      const saved = localStorage.getItem('securities-sort-model');
      return saved ? JSON.parse(saved) : [];
    } catch {
      return [];
    }
  });

  const setTagFilterPersisted = (tags: string[] | ((prev: string[]) => string[])) => {
    setTagFilter((prev) => {
      const next = typeof tags === 'function' ? tags(prev) : tags;
      localStorage.setItem('securities-tag-filter', JSON.stringify(next));
      return next;
    });
  };

  const { data: screenerData, isLoading: screenerLoading } = useScreener(screenerParams, screenerOpen && Object.keys(screenerParams).length > 0);
  const { mutate: refreshSnapshots, isPending: refreshing, data: refreshResult, reset: resetRefresh } = useRefreshOptionsSnapshots();

  const allTags = useMemo<string[]>(() => {
    const set = new Set<string>();
    (data?.securities ?? []).forEach((s) => s.tags.forEach((t) => set.add(t)));
    return Array.from(set).sort();
  }, [data]);

  const filtered = useMemo<Security[]>(() => {
    const list = data?.securities ?? [];
    return list.filter((s) => {
      if (sourceFilter !== 'all' && s.source !== sourceFilter) return false;
      if (tagFilter.length > 0 && !tagFilter.some((t) => s.tags.includes(t))) return false;
      if (search) {
        const q = search.toLowerCase();
        if (!s.symbol.toLowerCase().includes(q) && !s.name.toLowerCase().includes(q)) return false;
      }
      return true;
    });
  }, [data, sourceFilter, tagFilter, search]);

  const columns: GridColDef<Security>[] = [
    {
      field: 'symbol',
      headerName: 'Symbol',
      width: 100,
      renderCell: (p: GridRenderCellParams<Security, string>) => (
        <Typography
          variant="body2"
          sx={{ cursor: 'pointer', color: 'secondary.main', fontWeight: 600 }}
          onClick={() => navigate(`/securities/${p.value}`)}
        >
          {p.value}
        </Typography>
      ),
    },
    { field: 'name', headerName: 'Name', flex: 1, minWidth: 180 },
    {
      field: 'source',
      headerName: 'Source',
      width: 120,
      renderCell: (p: GridRenderCellParams<Security, string>) => (
        <Chip
          size="small"
          label={p.value}
          color={SOURCE_COLORS[p.value ?? 'watchlist']}
          icon={p.value === 'portfolio' || p.value === 'both'
            ? <AccountBalanceWalletIcon />
            : <VisibilityIcon />}
        />
      ),
    },
    { field: 'currency', headerName: 'Currency', width: 90 },
    {
      field: 'purchase_price',
      headerName: 'Cost Basis',
      width: 110,
      type: 'number',
      valueFormatter: (value: number | null) =>
        value != null ? `$${value.toFixed(2)}` : '—',
    },
    {
      field: 'quantity',
      headerName: 'Qty',
      width: 80,
      type: 'number',
      valueFormatter: (value: number | null) => value ?? '—',
    },
    {
      field: 'tags',
      headerName: 'Tags',
      flex: 1,
      minWidth: 200,
      sortable: false,
      renderCell: (p: GridRenderCellParams<Security, string[]>) => (
        <Stack
          direction="row" spacing={0.5} flexWrap="wrap" useFlexGap
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
              onClick={() => setTagFilterPersisted((prev) =>
                prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]
              )}
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
  ];

  if (isLoading) return <LoadingSpinner />;
  if (error) return <ErrorAlert message={(error as Error).message} />;

  return (
    <Box>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
        <Typography variant="h4">Securities</Typography>
        <Button
          variant="contained"
          startIcon={<AddIcon />}
          onClick={() => setAddOpen(true)}
        >
          Add Security
        </Button>
      </Stack>

      {/* Filter toolbar */}
      <Paper sx={{ p: 2, mb: 2 }}>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} alignItems="center">
          <TextField
            size="small"
            label="Search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            sx={{ minWidth: 180 }}
          />

          <ToggleButtonGroup
            size="small"
            exclusive
            value={sourceFilter}
            onChange={(_e, v) => { if (v) setSourceFilter(v as SourceFilter); }}
          >
            <ToggleButton value="all">All</ToggleButton>
            <ToggleButton value="portfolio">Portfolio</ToggleButton>
            <ToggleButton value="watchlist">Watchlist</ToggleButton>
            <ToggleButton value="both">Both</ToggleButton>
          </ToggleButtonGroup>

          <FormControl size="small" sx={{ minWidth: 220 }}>
            <InputLabel>Tags</InputLabel>
            <Select
              multiple
              value={tagFilter}
              onChange={(e: SelectChangeEvent<string[]>) =>
                setTagFilterPersisted(typeof e.target.value === 'string'
                  ? e.target.value.split(',')
                  : e.target.value)
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
              sx={{ ml: 'auto' }}
            >
              Clear Tags ({tagFilter.length})
            </Button>
          )}

          <Button
            size="small"
            startIcon={<FilterListIcon />}
            variant={screenerOpen ? 'contained' : 'outlined'}
            onClick={() => setScreenerOpen((o) => !o)}
            sx={{ ml: tagFilter.length > 0 ? 0 : 'auto' }}
          >
            Screener
          </Button>

          <Typography variant="body2" color="text.secondary">
            {filtered.length} of {data?.securities.length ?? 0} securities
          </Typography>
        </Stack>
      </Paper>

      {/* Technical Screener */}
      <Collapse in={screenerOpen}>
        <Paper sx={{ p: 2, mb: 2 }}>
          <Typography variant="subtitle2" gutterBottom>Technical Screener (from cached OHLCV data)</Typography>
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
            {SCREENER_PRESETS.map((preset) => (
              <Chip
                key={preset.label}
                label={preset.label}
                clickable
                variant={activePreset === preset.label ? 'filled' : 'outlined'}
                color={activePreset === preset.label ? 'primary' : 'default'}
                onClick={() => {
                  if (activePreset === preset.label) {
                    setActivePreset(null);
                    setScreenerParams({});
                  } else {
                    setActivePreset(preset.label);
                    setScreenerParams(preset.params as Record<string, string>);
                  }
                }}
                sx={{ fontSize: 12 }}
              />
            ))}
            {activePreset && (
              <Chip label="Clear" variant="outlined" clickable
                onClick={() => { setActivePreset(null); setScreenerParams({}); }}
                sx={{ fontSize: 12, borderColor: '#ef4444', color: '#ef4444' }} />
            )}
          </Stack>

          {screenerLoading && (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 2 }}>
              <CircularProgress size={18} />
              <Typography variant="body2" color="text.secondary">Scanning...</Typography>
            </Box>
          )}

          {screenerData && !screenerLoading && (
            <Box sx={{ mt: 2 }}>
              <Typography variant="body2" sx={{ mb: 1, color: '#9ca3af' }}>
                {screenerData.count} match{screenerData.count !== 1 ? 'es' : ''} — click to navigate
              </Typography>
              <Stack direction="row" flexWrap="wrap" spacing={1} useFlexGap>
                {screenerData.results.map((r: ScreenerResult) => (
                  <Chip
                    key={r.symbol}
                    label={
                      `${r.symbol}` +
                      (r.rsi != null ? ` · RSI ${r.rsi.toFixed(0)}` : '') +
                      (r.last_close != null ? ` · $${r.last_close.toFixed(2)}` : '')
                    }
                    clickable
                    onClick={() => navigate(`/securities/${r.symbol}`)}
                    sx={{
                      fontSize: 12,
                      bgcolor: r.rsi != null && r.rsi <= 30 ? '#1e3a2f'
                        : r.rsi != null && r.rsi >= 70 ? '#3a1e1e'
                        : '#1f2937',
                      color: '#f9fafb',
                    }}
                  />
                ))}
              </Stack>
            </Box>
          )}
        </Paper>
      </Collapse>

      {/* Options Snapshot Refresh */}
      <Paper sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" alignItems="center" spacing={2} flexWrap="wrap" useFlexGap>
          <Box sx={{ flex: 1, minWidth: 200 }}>
            <Typography variant="subtitle2">Options Snapshot Collection</Typography>
            <Typography variant="caption" sx={{ color: '#9ca3af' }}>
              Captures today's P/C ratio &amp; IV for all securities. Run once per trading day to build trend data.
              yfinance only provides the current chain — past snapshots cannot be backfilled.
            </Typography>
          </Box>
          <Button
            size="small"
            variant="outlined"
            startIcon={refreshing ? <CircularProgress size={14} /> : <RefreshIcon />}
            disabled={refreshing}
            onClick={() => { resetRefresh(); refreshSnapshots({ source: 'all', chainType: 'atm' }); }}
          >
            {refreshing ? 'Collecting…' : 'Refresh All (ATM)'}
          </Button>
          <Button
            size="small"
            variant="outlined"
            color="secondary"
            startIcon={refreshing ? <CircularProgress size={14} /> : <RefreshIcon />}
            disabled={refreshing}
            onClick={() => { resetRefresh(); refreshSnapshots({ source: 'portfolio', chainType: 'full' }); }}
          >
            {refreshing ? 'Collecting…' : 'Refresh Portfolio (Full Chain)'}
          </Button>
          {refreshResult && !refreshing && (
            <Stack direction="row" spacing={1} alignItems="center">
              <CheckCircleOutlineIcon sx={{ color: '#10b981', fontSize: 18 }} />
              <Typography variant="caption" sx={{ color: '#10b981' }}>
                {refreshResult.succeeded}/{refreshResult.total} succeeded in {refreshResult.duration_seconds}s
              </Typography>
              {refreshResult.failed > 0 && (
                <Typography variant="caption" sx={{ color: '#ef4444' }}>
                  · {refreshResult.failed} failed
                </Typography>
              )}
            </Stack>
          )}
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
          localStorage.setItem('securities-sort-model', JSON.stringify(model));
        }}
        onRowClick={(params) => navigate(`/securities/${params.row.symbol}`)}
        sx={{ cursor: 'pointer' }}
      />

      <AddSecurityDialog open={addOpen} onClose={() => setAddOpen(false)} />
    </Box>
  );
}
