import { useNavigate } from 'react-router-dom';
import { Grid, Card, CardContent, Typography, Box, Table, TableHead, TableRow, TableCell, TableBody, Chip } from '@mui/material';
import { useDashboard } from '../../hooks/useDashboard';
import { usePlans } from '../../hooks/usePlans';
import LoadingSpinner from '../common/LoadingSpinner';
import ErrorAlert from '../common/ErrorAlert';
import { formatCurrency, formatPercent } from '../../utils/formatting';

export default function DashboardPage() {
  const { data: stats, isLoading, error } = useDashboard();
  const { data: plansData } = usePlans('ACTIVE');
  const navigate = useNavigate();

  if (isLoading) return <LoadingSpinner />;
  if (error) return <ErrorAlert message={(error as Error).message} />;

  const cards = [
    { label: 'Active Plans', value: stats?.active_plans ?? 0, color: '#1976d2' },
    { label: 'Pending Rungs', value: stats?.pending_rungs ?? 0, color: '#ed6c02' },
    { label: 'Executed Rungs', value: stats?.executed_rungs ?? 0, color: '#2e7d32' },
    { label: 'Symbols Tracked', value: stats?.symbols_tracked ?? 0, color: '#9c27b0' },
  ];

  return (
    <Box>
      <Typography variant="h4" gutterBottom>Dashboard</Typography>
      <Grid container spacing={3} sx={{ mb: 4 }}>
        {cards.map((c) => (
          <Grid item xs={12} sm={6} md={3} key={c.label}>
            <Card>
              <CardContent>
                <Typography variant="body2" color="text.secondary">{c.label}</Typography>
                <Typography variant="h3" sx={{ color: c.color }}>{c.value}</Typography>
              </CardContent>
            </Card>
          </Grid>
        ))}
      </Grid>

      <Typography variant="h5" gutterBottom>Active Plans</Typography>
      {plansData?.plans.length ? (
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Symbol</TableCell>
              <TableCell>H Threshold</TableCell>
              <TableCell>Iterations</TableCell>
              <TableCell>Price</TableCell>
              <TableCell>Shares</TableCell>
              <TableCell>Status</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {plansData.plans.map((p) => (
              <TableRow
                key={p.instance_id}
                hover
                sx={{ cursor: 'pointer' }}
                onClick={() => navigate(`/plans/${p.instance_id}`)}
              >
                <TableCell><strong>{p.symbol}</strong></TableCell>
                <TableCell>{formatPercent(p.h_threshold)}</TableCell>
                <TableCell>{p.n_iterations}</TableCell>
                <TableCell>{formatCurrency(p.price_asof)}</TableCell>
                <TableCell>{p.shares_initial}</TableCell>
                <TableCell><Chip label={p.status} size="small" color="primary" /></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <Typography color="text.secondary">No active plans</Typography>
      )}
    </Box>
  );
}
