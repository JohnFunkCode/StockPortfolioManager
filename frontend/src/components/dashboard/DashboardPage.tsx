import { useNavigate } from 'react-router-dom';
import { Grid, Card, CardContent, Paper, Stack, Typography, Box, Table, TableHead, TableRow, TableCell, TableBody, Chip } from '@mui/material';
import { useDashboard } from '../../hooks/useDashboard';
import { usePlans } from '../../hooks/usePlans';
import { usePortfolioDeltaExposure } from '../../hooks/useSecurities';
import AgentHealthWidget from '../agents/AgentHealthWidget';
import LoadingSpinner from '../common/LoadingSpinner';
import ErrorAlert from '../common/ErrorAlert';
import { formatCurrency, formatPercent } from '../../utils/formatting';

export default function DashboardPage() {
  const { data: stats, isLoading, error } = useDashboard();
  const { data: plansData } = usePlans('ACTIVE');
  const { data: deltaData } = usePortfolioDeltaExposure();
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
        <Grid item xs={12} sm={6} md={3}>
          <AgentHealthWidget />
        </Grid>
      </Grid>

      {/* Portfolio Market Maker Exposure */}
      {deltaData && deltaData.positions.length > 0 && (
        <>
          <Typography variant="h5" gutterBottom sx={{ mt: 2 }}>
            Portfolio — Market Maker Delta Exposure
          </Typography>
          <Paper sx={{ p: 2, mb: 3 }}>
            <Stack direction="row" spacing={2} sx={{ mb: 2 }}>
              <Box>
                <Typography variant="caption" sx={{ color: '#6b7280' }}>Aggregate Net DAOI</Typography>
                <Typography variant="h5" sx={{
                  color: deltaData.portfolio_net_daoi > 0 ? '#10b981' : '#ef4444',
                  fontWeight: 700,
                }}>
                  {deltaData.portfolio_net_daoi > 0 ? '+' : ''}{deltaData.portfolio_net_daoi.toLocaleString()} shares
                </Typography>
                <Typography variant="caption" sx={{ color: '#9ca3af' }}>
                  {deltaData.portfolio_net_daoi > 0
                    ? 'Net positive: MMs net short delta — mechanical buy support on rallies'
                    : 'Net negative: MMs net long delta — selling pressure on rallies'}
                </Typography>
              </Box>
            </Stack>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Symbol</TableCell>
                  <TableCell align="right">Price</TableCell>
                  <TableCell align="right">Shares</TableCell>
                  <TableCell align="right">Net DAOI</TableCell>
                  <TableCell align="right">Call DAOI</TableCell>
                  <TableCell align="right">Put DAOI</TableCell>
                  <TableCell>MM Hedge Bias</TableCell>
                  <TableCell sx={{ color: '#6b7280', fontSize: 11 }}>Captured</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {deltaData.positions.map((pos) => (
                  <TableRow key={pos.symbol} hover sx={{ cursor: 'pointer' }}
                    onClick={() => navigate(`/securities/${pos.symbol}`)}>
                    <TableCell><strong>{pos.symbol}</strong></TableCell>
                    <TableCell align="right">${pos.price.toFixed(2)}</TableCell>
                    <TableCell align="right">{pos.shares || '—'}</TableCell>
                    <TableCell align="right" sx={{ color: pos.net_daoi_shares > 0 ? '#10b981' : '#ef4444', fontWeight: 600 }}>
                      {pos.net_daoi_shares > 0 ? '+' : ''}{pos.net_daoi_shares.toLocaleString()}
                    </TableCell>
                    <TableCell align="right" sx={{ color: '#3b82f6' }}>{pos.call_daoi.toLocaleString()}</TableCell>
                    <TableCell align="right" sx={{ color: '#f59e0b' }}>{pos.put_daoi.toLocaleString()}</TableCell>
                    <TableCell>
                      <Chip size="small"
                        label={pos.mm_hedge_bias === 'buy_on_rally' ? 'Buy on rally ↑' : 'Sell on rally ↓'}
                        sx={{
                          fontSize: 10, height: 18,
                          bgcolor: pos.mm_hedge_bias === 'buy_on_rally' ? '#1e3a2f' : '#3a1e1e',
                          color: pos.mm_hedge_bias === 'buy_on_rally' ? '#10b981' : '#ef4444',
                        }} />
                    </TableCell>
                    <TableCell sx={{ color: '#6b7280', fontSize: 11 }}>
                      {pos.captured_at ? pos.captured_at.slice(0, 10) : '—'}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Paper>
        </>
      )}

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
