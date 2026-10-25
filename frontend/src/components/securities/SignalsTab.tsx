/**
 * SignalsTab — Signals tab content for SecurityDetailPage.
 * Shows technical momentum, structure and options flow. Stops and drawdown
 * live in StopLossCard on the Technical Analysis tab (#399); the risk signals
 * still feed the summary line.
 *
 * Each section is its own component that takes `ticker` and calls its own
 * hooks; react-query dedupes the shared requests.
 */
import { Stack } from '@mui/material';
import InterpretationSummary from './InterpretationSummary';
import MomentumPanel from './MomentumPanel';
import StructurePanel from './StructurePanel';
import OptionsFlowPanel from './OptionsFlowPanel';
import NewsSentimentPanel from './NewsSentimentPanel';

export default function SignalsTab({ ticker }: { ticker: string }) {
  return (
    <Stack spacing={2}>
      <InterpretationSummary ticker={ticker} />
      <MomentumPanel ticker={ticker} />
      <StructurePanel ticker={ticker} />
      <OptionsFlowPanel ticker={ticker} />
      <NewsSentimentPanel ticker={ticker} />
    </Stack>
  );
}
