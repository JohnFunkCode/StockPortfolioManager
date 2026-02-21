import { useNavigate } from 'react-router-dom';
import { Box, Typography } from '@mui/material';
import { DataGrid, GridColDef } from '@mui/x-data-grid';
import { useSymbols } from '../../hooks/useSymbols';
import LoadingSpinner from '../common/LoadingSpinner';
import ErrorAlert from '../common/ErrorAlert';

export default function SymbolsPage() {
  const { data, isLoading, error } = useSymbols();
  const navigate = useNavigate();

  const columns: GridColDef[] = [
    { field: 'ticker', headerName: 'Ticker', width: 120 },
    { field: 'name', headerName: 'Name', width: 200 },
    { field: 'currency', headerName: 'Currency', width: 100 },
    {
      field: 'active_plan_id',
      headerName: 'Active Plan',
      width: 140,
      renderCell: (params) =>
        params.value ? (
          <Typography
            variant="body2"
            sx={{ cursor: 'pointer', color: 'primary.main', textDecoration: 'underline' }}
            onClick={() => navigate(`/plans/${params.value}`)}
          >
            Plan #{params.value}
          </Typography>
        ) : (
          <Typography variant="body2" color="text.secondary">None</Typography>
        ),
    },
  ];

  if (isLoading) return <LoadingSpinner />;
  if (error) return <ErrorAlert message={(error as Error).message} />;

  return (
    <Box>
      <Typography variant="h4" gutterBottom>Symbols</Typography>
      <DataGrid
        rows={data?.symbols ?? []}
        columns={columns}
        getRowId={(row) => row.symbol_id}
        autoHeight
        disableRowSelectionOnClick
        pageSizeOptions={[10, 25]}
        initialState={{ pagination: { paginationModel: { pageSize: 25 } } }}
      />
    </Box>
  );
}
