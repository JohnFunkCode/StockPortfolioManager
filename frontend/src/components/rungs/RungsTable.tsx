import { useState } from 'react';
import { DataGrid, GridColDef } from '@mui/x-data-grid';
import { Button } from '@mui/material';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import RungStatusChip from './RungStatusChip';
import AchieveRungDialog from './AchieveRungDialog';
import ExecuteRungDialog from './ExecuteRungDialog';
import { formatCurrency, formatPercent, formatDate } from '../../utils/formatting';
import type { Rung } from '../../api/types';

export default function RungsTable({ rungs }: { rungs: Rung[] }) {
  const [achieveRung, setAchieveRung] = useState<Rung | null>(null);
  const [executeRung, setExecuteRung] = useState<Rung | null>(null);

  const columns: GridColDef[] = [
    { field: 'rung_index', headerName: '#', width: 60 },
    {
      field: 'status',
      headerName: 'Status',
      width: 110,
      renderCell: (params) => <RungStatusChip status={params.value} />,
    },
    {
      field: 'target_price',
      headerName: 'Target Price',
      width: 120,
      valueFormatter: (value: number) => formatCurrency(value),
    },
    { field: 'shares_before', headerName: 'Shares Before', width: 120 },
    { field: 'shares_sold_planned', headerName: 'Sell', width: 80 },
    { field: 'shares_after_planned', headerName: 'After', width: 80 },
    {
      field: 'expected_date',
      headerName: 'Expected Date',
      width: 130,
      valueFormatter: (value: string) => formatDate(value),
    },
    {
      field: 'gross_harvest_planned',
      headerName: 'Harvest',
      width: 120,
      valueFormatter: (value: number) => formatCurrency(value),
    },
    {
      field: 'cumulative_harvest_planned',
      headerName: 'Cumulative',
      width: 120,
      valueFormatter: (value: number) => formatCurrency(value),
    },
    {
      field: 'total_return_planned',
      headerName: 'Return',
      width: 100,
      valueFormatter: (value: number) => formatPercent(value),
    },
    {
      field: 'actions',
      headerName: 'Actions',
      width: 160,
      sortable: false,
      renderCell: (params) => {
        const rung = params.row as Rung;
        if (rung.status === 'PENDING') {
          return (
            <Button size="small" startIcon={<CheckCircleIcon />} onClick={() => setAchieveRung(rung)}>
              Achieve
            </Button>
          );
        }
        if (rung.status === 'ACHIEVED') {
          return (
            <Button size="small" color="success" startIcon={<PlayArrowIcon />} onClick={() => setExecuteRung(rung)}>
              Execute
            </Button>
          );
        }
        return null;
      },
    },
  ];

  return (
    <>
      <DataGrid
        rows={rungs}
        columns={columns}
        getRowId={(row) => row.rung_id}
        autoHeight
        disableRowSelectionOnClick
        pageSizeOptions={[10]}
        initialState={{ pagination: { paginationModel: { pageSize: 10 } } }}
      />
      {achieveRung && (
        <AchieveRungDialog
          open
          rungId={achieveRung.rung_id}
          targetPrice={achieveRung.target_price}
          onClose={() => setAchieveRung(null)}
        />
      )}
      {executeRung && (
        <ExecuteRungDialog
          open
          rungId={executeRung.rung_id}
          sharesSoldPlanned={executeRung.shares_sold_planned}
          targetPrice={executeRung.target_price}
          onClose={() => setExecuteRung(null)}
        />
      )}
    </>
  );
}
