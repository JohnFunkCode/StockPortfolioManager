/**
 * The Signals tab's "Interpretation" box: one sentence per signal family,
 * written by `buildSignalsSummary`. It reads the same queries the panels do
 * (react-query shares them) and waits for technical, flow and risk before it
 * says anything; news is not waited on — it scores headlines live and can take
 * a minute, so its sentence joins when it lands.
 */
import { Paper, Stack, Typography } from '@mui/material';
import { useNews, useOptionsFlowSignals, useRiskSignals, useTechnicalSignals } from '../../hooks/useSecurities';
import { buildSignalsSummary } from './signalsSummary';

export default function InterpretationSummary({ ticker }: { ticker: string }) {
  const tech = useTechnicalSignals(ticker, true);
  const flow = useOptionsFlowSignals(ticker, true);
  const risk = useRiskSignals(ticker, true);
  const { data: news } = useNews(ticker);

  if (tech.isLoading || flow.isLoading || risk.isLoading) return null;
  const lines = buildSignalsSummary(tech.data, flow.data, risk.data, news);
  if (lines.length === 0) return null;

  return (
    <Paper variant="outlined" sx={{ p: 2, borderColor: 'divider', bgcolor: 'background.default' }}>
      <Typography variant="subtitle2" sx={{ mb: 1, color: 'text.secondary' }}>
        Interpretation
      </Typography>
      <Stack spacing={0.75}>
        {lines.map((line, i) => (
          <Typography key={i} variant="body2" sx={{ color: 'text.primary', lineHeight: 1.6 }}>
            {line}
          </Typography>
        ))}
      </Stack>
    </Paper>
  );
}
