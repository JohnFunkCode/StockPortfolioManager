import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Box, Typography, Button, Chip, ToggleButtonGroup, ToggleButton, Stack, Tooltip } from '@mui/material';
import { DataGrid, GridColDef } from '@mui/x-data-grid';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import { usePlans, useDeletePlan } from '../../hooks/usePlans';
import CreatePlanDialog from './CreatePlanDialog';
import EditNotesDialog from './EditNotesDialog';
import ConfirmDialog from '../common/ConfirmDialog';
import LoadingSpinner from '../common/LoadingSpinner';
import ErrorAlert from '../common/ErrorAlert';
import { formatCurrency, formatPercent, formatDate } from '../../utils/formatting';
import type { Plan } from '../../api/types';

export default function PlansPage() {
  const [status, setStatus] = useState('ACTIVE');
  const [createOpen, setCreateOpen] = useState(false);
  const [editPlan, setEditPlan] = useState<Plan | null>(null);
  const [deletePlanId, setDeletePlanId] = useState<number | null>(null);
  const { data, isLoading, error } = usePlans(status);
  const deleteMutation = useDeletePlan();
  const navigate = useNavigate();

  const columns: GridColDef[] = [
    { field: 'symbol', headerName: 'Symbol', width: 100, sortable: true },
    {
      field: 'status', headerName: 'Status', width: 190,
      renderCell: (params) => {
        const plan = params.row as Plan;
        // Orphan flag (issue #147 Part H7). `in_portfolio === false` is the
        // only case worth flagging — undefined means the server did not say,
        // which is not the same as "they sold out of it". Flag only: no filter,
        // no bulk action. Closing a live ladder is the owner's call.
        const orphan = plan.status === 'ACTIVE' && plan.in_portfolio === false;
        return (
          <Stack direction="row" spacing={0.5} alignItems="center">
            <span>{plan.status}</span>
            {orphan && (
              <Tooltip title="No open position in this symbol — the ladder has no shares under it.">
                <Chip
                  size="small"
                  label="No position"
                  sx={{ bgcolor: '#3a2f1e', color: '#f59e0b' }}
                />
              </Tooltip>
            )}
          </Stack>
        );
      },
    },
    {
      field: 'created_at', headerName: 'Created', width: 140,
      valueFormatter: (value: string) => formatDate(value),
    },
    {
      field: 'h_threshold', headerName: 'H Threshold', width: 120,
      valueFormatter: (value: number) => formatPercent(value),
    },
    { field: 'n_iterations', headerName: 'Iters', width: 80 },
    {
      field: 'price_asof', headerName: 'Price', width: 110,
      valueFormatter: (value: number) => formatCurrency(value),
    },
    { field: 'shares_initial', headerName: 'Shares', width: 90 },
    {
      field: 'actions', headerName: 'Actions', width: 160, sortable: false,
      renderCell: (params) => {
        const plan = params.row as Plan;
        return (
          <Stack direction="row" spacing={0.5}>
            <Button
              size="small"
              startIcon={<EditIcon />}
              onClick={(e) => { e.stopPropagation(); setEditPlan(plan); }}
            >
              Notes
            </Button>
            {plan.status === 'ACTIVE' && (
              <Button
                size="small"
                color="error"
                startIcon={<DeleteIcon />}
                onClick={(e) => { e.stopPropagation(); setDeletePlanId(plan.instance_id); }}
              >
                Archive
              </Button>
            )}
          </Stack>
        );
      },
    },
  ];

  if (isLoading) return <LoadingSpinner />;
  if (error) return <ErrorAlert message={(error as Error).message} />;

  return (
    <Box>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
        <Typography variant="h4">Plans</Typography>
        <Button variant="contained" startIcon={<AddIcon />} onClick={() => setCreateOpen(true)}>
          New Plan
        </Button>
      </Stack>

      <ToggleButtonGroup
        value={status}
        exclusive
        onChange={(_, v) => v && setStatus(v)}
        size="small"
        sx={{ mb: 2 }}
      >
        <ToggleButton value="ACTIVE">Active</ToggleButton>
        <ToggleButton value="SUPERSEDED">Superseded</ToggleButton>
        <ToggleButton value="CLOSED">Closed</ToggleButton>
        <ToggleButton value="ALL">All</ToggleButton>
      </ToggleButtonGroup>

      <DataGrid
        rows={data?.plans ?? []}
        columns={columns}
        getRowId={(row) => row.instance_id}
        autoHeight
        disableRowSelectionOnClick
        onRowClick={(params) => navigate(`/plans/${params.id}`)}
        pageSizeOptions={[10, 25]}
        initialState={{ pagination: { paginationModel: { pageSize: 10 } } }}
        sx={{ cursor: 'pointer' }}
      />

      <CreatePlanDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(id) => navigate(`/plans/${id}`)}
      />

      {editPlan && (
        <EditNotesDialog
          open
          planId={editPlan.instance_id}
          currentNotes={editPlan.notes}
          onClose={() => setEditPlan(null)}
        />
      )}

      <ConfirmDialog
        open={deletePlanId !== null}
        title="Archive Plan"
        message="This will set the plan status to SUPERSEDED. Are you sure?"
        confirmLabel="Archive"
        loading={deleteMutation.isPending}
        onConfirm={() => {
          if (deletePlanId) deleteMutation.mutate(deletePlanId, { onSuccess: () => setDeletePlanId(null) });
        }}
        onCancel={() => setDeletePlanId(null)}
      />
    </Box>
  );
}
