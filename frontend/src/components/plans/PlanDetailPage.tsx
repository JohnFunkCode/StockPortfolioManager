import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Box, Typography, Card, CardContent, Grid, Chip, Button, Stack, Divider,
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import EditIcon from '@mui/icons-material/Edit';
import DeleteIcon from '@mui/icons-material/Delete';
import { usePlan, useDeletePlan } from '../../hooks/usePlans';
import RungsTable from '../rungs/RungsTable';
import LivePrice from '../symbols/LivePrice';
import EditNotesDialog from './EditNotesDialog';
import ConfirmDialog from '../common/ConfirmDialog';
import LoadingSpinner from '../common/LoadingSpinner';
import ErrorAlert from '../common/ErrorAlert';
import { formatCurrency, formatPercent, formatDate } from '../../utils/formatting';

export default function PlanDetailPage() {
  const { id } = useParams<{ id: string }>();
  const planId = Number(id);
  const { data, isLoading, error } = usePlan(planId);
  const deleteMutation = useDeletePlan();
  const navigate = useNavigate();
  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  if (isLoading) return <LoadingSpinner />;
  if (error) return <ErrorAlert message={(error as Error).message} />;
  if (!data) return <ErrorAlert message="Plan not found" />;

  const { plan, rungs } = data;

  const fields = [
    { label: 'Status', value: <Chip label={plan.status} size="small" color={plan.status === 'ACTIVE' ? 'primary' : 'default'} /> },
    { label: 'H Threshold', value: formatPercent(plan.h_threshold) },
    { label: 'Annual Volatility', value: formatPercent(plan.annual_vol) },
    { label: 'Daily Growth', value: `${(plan.r_daily * 100).toFixed(4)}%` },
    { label: 'Iterations', value: plan.n_iterations },
    { label: 'Initial Shares', value: plan.shares_initial },
    { label: 'Floor Value (V0)', value: formatCurrency(plan.v0_floor) },
    { label: 'Price As Of', value: formatCurrency(plan.price_asof) },
    { label: 'Created', value: formatDate(plan.created_at) },
    { label: 'History End', value: formatDate(plan.history_end_date) },
  ];

  return (
    <Box>
      <Button startIcon={<ArrowBackIcon />} onClick={() => navigate('/plans')} sx={{ mb: 2 }}>
        Back to Plans
      </Button>

      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
        <Stack direction="row" spacing={2} alignItems="center">
          <Typography variant="h4">{plan.symbol}</Typography>
          <LivePrice ticker={plan.symbol} />
        </Stack>
        <Stack direction="row" spacing={1}>
          <Button startIcon={<EditIcon />} onClick={() => setEditOpen(true)}>Notes</Button>
          {plan.status === 'ACTIVE' && (
            <Button color="error" startIcon={<DeleteIcon />} onClick={() => setDeleteOpen(true)}>
              Archive
            </Button>
          )}
        </Stack>
      </Stack>

      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Grid container spacing={2}>
            {fields.map((f) => (
              <Grid item xs={6} sm={4} md={3} key={f.label}>
                <Typography variant="caption" color="text.secondary">{f.label}</Typography>
                <Typography variant="body1">{f.value}</Typography>
              </Grid>
            ))}
          </Grid>
          {plan.notes && (
            <>
              <Divider sx={{ my: 2 }} />
              <Typography variant="caption" color="text.secondary">Notes</Typography>
              <Typography variant="body2">{plan.notes}</Typography>
            </>
          )}
        </CardContent>
      </Card>

      <Typography variant="h5" gutterBottom>Rungs</Typography>
      <RungsTable rungs={rungs} />

      {editOpen && (
        <EditNotesDialog open planId={planId} currentNotes={plan.notes} onClose={() => setEditOpen(false)} />
      )}

      <ConfirmDialog
        open={deleteOpen}
        title="Archive Plan"
        message={`Archive the ${plan.symbol} plan? It will be marked as SUPERSEDED.`}
        confirmLabel="Archive"
        loading={deleteMutation.isPending}
        onConfirm={() => {
          deleteMutation.mutate(planId, { onSuccess: () => navigate('/plans') });
        }}
        onCancel={() => setDeleteOpen(false)}
      />
    </Box>
  );
}
