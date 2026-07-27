/**
 * ScanTable — the ranked scan, one row per curated pair.
 *
 * Columns are chosen so a `reject` is legible at a glance: the score alone
 * says little, but score + convergence + hedgeability + what breaks it says
 * why. `discount_pct` is the NET figure; the gross number never reaches this
 * projection.
 */
import { Box, Chip, Stack, Tooltip, Typography } from '@mui/material';
import { DataGrid, GridColDef, GridRenderCellParams } from '@mui/x-data-grid';
import type { ScanRow } from '../../api/arbitrage';

const VERDICT_COLOR: Record<string, 'success' | 'warning' | 'default'> = {
  candidate: 'success',
  watch: 'warning',
  reject: 'default',
};

interface Props {
  rows: ScanRow[];
  onSelect?: (security: string) => void;
}

export default function ScanTable({ rows, onSelect }: Props) {
  const columns: GridColDef<ScanRow>[] = [
    { field: 'security', headerName: 'Symbol', width: 90 },
    { field: 'underlying', headerName: 'Underlying', width: 100 },
    {
      field: 'kind',
      headerName: 'Family',
      width: 130,
      valueFormatter: (value: string) => value?.replace('_', ' '),
    },
    {
      field: 'score',
      headerName: 'Score',
      width: 80,
      type: 'number',
      valueFormatter: (value: number | null) => (value == null ? '—' : value.toFixed(1)),
    },
    {
      field: 'verdict',
      headerName: 'Verdict',
      width: 110,
      renderCell: (p: GridRenderCellParams<ScanRow, string>) => (
        <Chip size="small" label={p.value} color={VERDICT_COLOR[p.value ?? ''] ?? 'default'} />
      ),
    },
    {
      field: 'discount_pct',
      headerName: 'Net discount',
      width: 120,
      type: 'number',
      valueFormatter: (value: number | null) =>
        value == null ? '—' : `${value.toFixed(2)}%`,
    },
    { field: 'convergence', headerName: 'Convergence', width: 130 },
    {
      field: 'hedge_available',
      headerName: 'Hedge',
      width: 120,
      renderCell: (p: GridRenderCellParams<ScanRow, boolean>) =>
        p.value ? (
          <Typography variant="body2">{p.row.hedge_instrument ?? '—'}</Typography>
        ) : (
          <Chip size="small" label="futures only" color="warning" variant="outlined" />
        ),
    },
    {
      field: 'breaks_on',
      headerName: 'What breaks it',
      flex: 1,
      minWidth: 180,
      sortable: false,
      renderCell: (p: GridRenderCellParams<ScanRow, string[]>) => {
        const risks = p.value ?? [];
        if (risks.length === 0) return <Typography variant="body2">—</Typography>;
        return (
          <Tooltip title={risks.join(' · ')}>
            <Stack direction="row" spacing={0.5} alignItems="center" sx={{ height: '100%' }}>
              <Typography variant="caption" noWrap>
                {risks[0]}
              </Typography>
              {risks.length > 1 && <Chip size="small" label={`+${risks.length - 1}`} />}
            </Stack>
          </Tooltip>
        );
      },
    },
  ];

  return (
    <Box data-testid="scan-table">
      <DataGrid
        rows={rows}
        columns={columns}
        getRowId={(row) => row.security}
        autoHeight
        disableRowSelectionOnClick
        hideFooter={rows.length <= 25}
        onRowClick={(params) => onSelect?.(String(params.id))}
        sx={{ cursor: onSelect ? 'pointer' : 'default' }}
      />
    </Box>
  );
}
