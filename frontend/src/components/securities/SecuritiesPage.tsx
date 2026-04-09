import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box,
  Chip,
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
import { DataGrid, GridColDef, GridRenderCellParams } from '@mui/x-data-grid';
import AccountBalanceWalletIcon from '@mui/icons-material/AccountBalanceWallet';
import VisibilityIcon from '@mui/icons-material/Visibility';
import type { SelectChangeEvent } from '@mui/material';
import { useSecurities } from '../../hooks/useSecurities';
import LoadingSpinner from '../common/LoadingSpinner';
import ErrorAlert from '../common/ErrorAlert';
import type { Security } from '../../api/securitiesTypes';

type SourceFilter = 'all' | 'portfolio' | 'watchlist' | 'both';

const SOURCE_COLORS: Record<string, 'primary' | 'success' | 'secondary'> = {
  portfolio: 'primary',
  watchlist: 'success',
  both: 'secondary',
};

export default function SecuritiesPage() {
  const navigate = useNavigate();
  const { data, isLoading, error } = useSecurities();

  const [sourceFilter, setSourceFilter] = useState<SourceFilter>('all');
  const [tagFilter, setTagFilter] = useState<string[]>([]);
  const [search, setSearch] = useState('');

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
          sx={{ cursor: 'pointer', color: 'primary.main', fontWeight: 600 }}
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
        <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
          {(p.value ?? []).slice(0, 4).map((t) => (
            <Chip key={t} label={t} size="small" variant="outlined" sx={{ fontSize: 11 }} />
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
      <Typography variant="h4" gutterBottom>Securities</Typography>

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
                setTagFilter(typeof e.target.value === 'string'
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

          <Typography variant="body2" color="text.secondary" sx={{ ml: 'auto' }}>
            {filtered.length} of {data?.securities.length ?? 0} securities
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
        onRowClick={(params) => navigate(`/securities/${params.row.symbol}`)}
        sx={{ cursor: 'pointer' }}
      />
    </Box>
  );
}
