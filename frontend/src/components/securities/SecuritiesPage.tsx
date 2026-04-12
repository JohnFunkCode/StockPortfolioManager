import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import AddSecurityDialog from './AddSecurityDialog';
import {
  Alert,
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
import { useSecurities, useScreener, useRefreshOptionsSnapshots, useSentimentSummary } from '../../hooks/useSecurities';
import LoadingSpinner from '../common/LoadingSpinner';
import ErrorAlert from '../common/ErrorAlert';
import type { Security, ScreenerResult, SentimentSummaryItem } from '../../api/securitiesTypes';

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
  { label: 'News: Positive',         params: { news_sentiment: 'positive' } },
  { label: 'News: Negative',         params: { news_sentiment: 'negative' } },
] as const;

const SENTIMENT_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  positive: { bg: '#1e3a2f', text: '#10b981', border: '#10b981' },
  negative: { bg: '#3a1e1e', text: '#ef4444', border: '#ef4444' },
  neutral:  { bg: '#1f2937', text: '#9ca3af', border: '#4b5563' },
};

function SentimentBadge({ sentiment }: { sentiment: string | null | undefined }) {
  if (!sentiment) return <Typography variant="body2" sx={{ color: '#4b5563' }}>—</Typography>;
  const c = SENTIMENT_COLORS[sentiment] ?? SENTIMENT_COLORS.neutral;
  const label = sentiment === 'positive' ? '↑ pos' : sentiment === 'negative' ? '↓ neg' : '– neu';
  return (
    <Chip size="small" label={label}
      sx={{ fontSize: 11, height: 20, bgcolor: c.bg, color: c.text, border: `1px solid ${c.border}` }} />
  );
}

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
  const [sentimentOpen, setSentimentOpen] = useState(false);

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
  const { mutate: refreshSnapshots, isPending: refreshing, data: refreshResult, error: refreshError, reset: resetRefresh } = useRefreshOptionsSnapshots();
  const { data: sentimentData, isLoading: sentimentLoading, refetch: refetchSentiment } = useSentimentSummary('all');

  // Build a symbol→sentiment map for the DataGrid badge column
  const sentimentMap = useMemo<Record<string, string>>(() => {
    const map: Record<string, string> = {};
    (sentimentData?.items ?? []).forEach((item) => {
      if (item.overall_sentiment) map[item.symbol] = item.overall_sentiment;
    });
    return map;
  }, [sentimentData]);

  const allTags = useMemo<string[]>(() => {
    const set = new Set<string>();
    (data?.securities ?? []).forEach((s) => s.tags.forEach((t) => set.add(t)));
    return Array.from(set).sort();
  }, [data]);

  const filtered = useMemo<Security[]>(() => {
    const list = data?.securities ?? [];
    return list.filter((s) => {
      if (sourceFilter !== 'all') {
        if (sourceFilter === 'portfolio' && s.source !== 'portfolio' && s.source !== 'both') return false;
        if (sourceFilter === 'watchlist' && s.source !== 'watchlist' && s.source !== 'both') return false;
        if (sourceFilter === 'both' && s.source !== 'both') return false;
      }
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
          sx={{ cursor: 'pointer', color: 'secondary.main', fontWeight: 600, lineHeight: '52px' }}
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
      field: '_sentiment',
      headerName: 'News',
      width: 90,
      sortable: false,
      renderCell: (p: GridRenderCellParams<Security>) => (
        <SentimentBadge sentiment={sentimentMap[p.row.symbol]} />
      ),
    },
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
          alignItems="center" sx={{ height: '100%' }}
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

          <Button
            size="small"
            variant={sentimentOpen ? 'contained' : 'outlined'}
            color="secondary"
            onClick={() => setSentimentOpen((o) => !o)}
          >
            Sentiment
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
                {screenerData.results.map((r: ScreenerResult) => {
                  const sentColor = r.news_sentiment
                    ? SENTIMENT_COLORS[r.news_sentiment]?.bg
                    : undefined;
                  const bg = sentColor
                    ?? (r.rsi != null && r.rsi <= 30 ? '#1e3a2f'
                      : r.rsi != null && r.rsi >= 70 ? '#3a1e1e'
                      : '#1f2937');
                  return (
                    <Chip
                      key={r.symbol}
                      label={
                        `${r.symbol}` +
                        (r.news_sentiment ? ` · ${r.news_sentiment}` : '') +
                        (r.rsi != null ? ` · RSI ${r.rsi.toFixed(0)}` : '') +
                        (r.last_close != null ? ` · $${r.last_close.toFixed(2)}` : '')
                      }
                      clickable
                      onClick={() => navigate(`/securities/${r.symbol}`)}
                      sx={{ fontSize: 12, bgcolor: bg, color: '#f9fafb' }}
                    />
                  );
                })}
              </Stack>
            </Box>
          )}
        </Paper>
      </Collapse>

      {/* Sentiment Dashboard */}
      <Collapse in={sentimentOpen}>
        <Paper sx={{ p: 2, mb: 2 }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1.5 }}>
            <Typography variant="subtitle2">News Sentiment Dashboard (FinBERT)</Typography>
            <Button size="small" startIcon={<RefreshIcon />} onClick={() => refetchSentiment()} disabled={sentimentLoading}>
              Refresh
            </Button>
          </Stack>

          {sentimentLoading && (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <CircularProgress size={18} />
              <Typography variant="body2" color="text.secondary">Loading sentiment data…</Typography>
            </Box>
          )}

          {!sentimentLoading && sentimentData && sentimentData.count === 0 && (
            <Typography variant="body2" color="text.secondary">
              No sentiment data yet — open a security's Signals tab to score its news.
            </Typography>
          )}

          {!sentimentLoading && sentimentData && sentimentData.count > 0 && (
            <>
              {/* Summary counts */}
              <Stack direction="row" spacing={2} sx={{ mb: 1.5 }}>
                {(['negative', 'neutral', 'positive'] as const).map((s) => {
                  const count = sentimentData.items.filter((i) => i.overall_sentiment === s).length;
                  const c = SENTIMENT_COLORS[s];
                  return (
                    <Chip key={s} size="small"
                      label={`${s === 'positive' ? '↑' : s === 'negative' ? '↓' : '–'} ${count} ${s}`}
                      sx={{ fontSize: 11, bgcolor: c.bg, color: c.text, border: `1px solid ${c.border}` }} />
                  );
                })}
                <Typography variant="caption" sx={{ color: '#6b7280', alignSelf: 'center' }}>
                  {sentimentData.count} scored securities
                </Typography>
              </Stack>

              {/* Ranked table — negative first */}
              <Box sx={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                  <thead>
                    <tr style={{ color: '#6b7280', textAlign: 'left', borderBottom: '1px solid #1f2937' }}>
                      <th style={{ padding: '4px 8px' }}>Symbol</th>
                      <th style={{ padding: '4px 8px' }}>Name</th>
                      <th style={{ padding: '4px 8px' }}>Sentiment</th>
                      <th style={{ padding: '4px 8px', textAlign: 'center' }}>↑</th>
                      <th style={{ padding: '4px 8px', textAlign: 'center' }}>↓</th>
                      <th style={{ padding: '4px 8px', textAlign: 'center' }}>–</th>
                      <th style={{ padding: '4px 8px' }}>Scored</th>
                      <th style={{ padding: '4px 8px' }}>As of</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sentimentData.items.map((item: SentimentSummaryItem) => {
                      const c = SENTIMENT_COLORS[item.overall_sentiment ?? 'neutral'];
                      return (
                        <tr key={item.symbol}
                          style={{ borderBottom: '1px solid #111827', cursor: 'pointer' }}
                          onClick={() => navigate(`/securities/${item.symbol}`)}
                        >
                          <td style={{ padding: '4px 8px', color: '#818cf8', fontWeight: 600 }}>{item.symbol}</td>
                          <td style={{ padding: '4px 8px', color: '#d1d5db', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.name}</td>
                          <td style={{ padding: '4px 8px' }}>
                            <span style={{ background: c.bg, color: c.text, border: `1px solid ${c.border}`, borderRadius: 4, padding: '1px 6px', fontSize: 11 }}>
                              {item.overall_sentiment ?? '—'}
                            </span>
                          </td>
                          <td style={{ padding: '4px 8px', textAlign: 'center', color: '#10b981' }}>{item.positive_count}</td>
                          <td style={{ padding: '4px 8px', textAlign: 'center', color: '#ef4444' }}>{item.negative_count}</td>
                          <td style={{ padding: '4px 8px', textAlign: 'center', color: '#6b7280' }}>{item.neutral_count}</td>
                          <td style={{ padding: '4px 8px', color: '#9ca3af' }}>{item.scored_count}</td>
                          <td style={{ padding: '4px 8px', color: '#4b5563' }}>{item.captured_at?.slice(0, 10) ?? '—'}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </Box>
            </>
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
          {refreshError && !refreshing && (
            <Alert severity="error" onClose={resetRefresh} sx={{ fontSize: 12, py: 0 }}>
              {(refreshError as Error).message || 'Refresh failed — check that the API server is running.'}
            </Alert>
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
