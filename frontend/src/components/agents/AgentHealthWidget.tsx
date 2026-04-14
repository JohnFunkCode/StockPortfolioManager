/**
 * AgentHealthWidget — compact dashboard card showing market status
 * and circuit breaker states from GET /api/agents/health.
 */
import {
  Box,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material';
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord';
import { useAgentHealth } from '../../hooks/useAgents';

function StatusDot({ open }: { open: boolean }) {
  return (
    <FiberManualRecordIcon
      sx={{ fontSize: 10, color: open ? '#10b981' : '#6b7280', mt: 0.3 }}
    />
  );
}

export default function AgentHealthWidget() {
  const { data, isLoading, error } = useAgentHealth();

  if (isLoading) {
    return (
      <Card>
        <CardContent sx={{ display: 'flex', alignItems: 'center', gap: 1, py: '12px !important' }}>
          <CircularProgress size={14} />
          <Typography variant="caption" color="text.secondary">Agent system…</Typography>
        </CardContent>
      </Card>
    );
  }

  if (error || !data) return null;

  const breakers = Object.entries(data.circuit_breakers);
  const openBreakers = breakers.filter(([, s]) => s.state === 'open');

  return (
    <Card>
      <CardContent sx={{ py: '12px !important' }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 1 }}>
          <Typography variant="caption" sx={{ color: '#9ca3af', textTransform: 'uppercase', letterSpacing: 1 }}>
            Agent System
          </Typography>
          <Chip
            size="small"
            label={data.market_open ? 'Market Open' : 'Market Closed'}
            sx={{
              fontSize: 10,
              height: 18,
              bgcolor: data.market_open ? '#1e3a2f' : '#3a2a1e',
              color:   data.market_open ? '#10b981'  : '#f59e0b',
            }}
          />
        </Stack>

        {breakers.length === 0 ? (
          <Typography variant="caption" color="text.secondary">No circuit breakers configured</Typography>
        ) : (
          <Stack spacing={0.5}>
            {openBreakers.length > 0 && (
              <Typography variant="caption" sx={{ color: '#ef4444', fontWeight: 600 }}>
                {openBreakers.length} breaker{openBreakers.length > 1 ? 's' : ''} open
              </Typography>
            )}
            <Stack direction="row" flexWrap="wrap" gap={0.75}>
              {breakers.map(([tool, state]) => (
                <Tooltip
                  key={tool}
                  title={
                    state.state === 'open'
                      ? `Open — resets in ${state.resets_in_seconds ?? '?'}s`
                      : `Closed — ${state.error_count ?? 0} errors`
                  }
                >
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.4, cursor: 'default' }}>
                    <FiberManualRecordIcon
                      sx={{
                        fontSize: 9,
                        color: state.state === 'open' ? '#ef4444' : '#10b981',
                      }}
                    />
                    <Typography variant="caption" sx={{ fontSize: 11, color: '#9ca3af' }}>
                      {tool}
                    </Typography>
                  </Box>
                </Tooltip>
              ))}
            </Stack>
          </Stack>
        )}

        {openBreakers.length === 0 && breakers.length > 0 && (
          <Typography variant="caption" sx={{ color: '#10b981', fontSize: 11 }}>
            All tools nominal
          </Typography>
        )}
      </CardContent>
    </Card>
  );
}
