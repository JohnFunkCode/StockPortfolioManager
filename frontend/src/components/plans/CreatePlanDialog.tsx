import { useEffect, useMemo, useState } from 'react';
import {
  Alert, Dialog, DialogTitle, DialogContent, DialogActions, Button, MenuItem, TextField, Stack,
  Accordion, AccordionSummary, AccordionDetails, Typography,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import { useCreatePlan } from '../../hooks/usePlans';
import { useSecurities } from '../../hooks/useSecurities';

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated?: (instanceId: number) => void;
  /** Pre-selects the symbol — the Portfolio page's "Create plan" affordance
   *  (issue #147 Part H6) already knows which row was clicked. */
  initialSymbol?: string;
}

export default function CreatePlanDialog({ open, onClose, onCreated, initialSymbol }: Props) {
  const [symbol, setSymbol] = useState(initialSymbol ?? '');
  const [templateName, setTemplateName] = useState('Default Template');
  const [historyDays, setHistoryDays] = useState('360');
  const [iterations, setIterations] = useState('4');
  const [alpha, setAlpha] = useState('0.5');
  const [minH, setMinH] = useState('0.05');
  const [maxH, setMaxH] = useState('0.30');
  const [maxS0, setMaxS0] = useState('1000');
  const mutation = useCreatePlan();

  // The holding invariant (issue #147 Part H5) lives in the service, which
  // answers a plan on an unheld symbol with a 422. This picker is the polite
  // half of that: it only offers symbols the caller actually holds, so the
  // usual way to hit the 422 stops existing. It is a convenience, never the
  // enforcement — see the free-text fallback below.
  // Gated on `open`: the dialog stays mounted while shut, and a closed dialog
  // has no business fetching. `isPending` (not `isLoading`) is what tells the
  // field to wait — a disabled query is never "loading", and treating that as
  // "loaded and empty" would flash the no-holdings notice on every open.
  const { data: held, isPending: holdingsPending, error: holdingsError } = useSecurities('portfolio', open);
  const symbols = useMemo(
    () => (held?.securities ?? []).map((s) => s.symbol).sort((a, b) => a.localeCompare(b)),
    [held],
  );
  // Degrade to free text rather than lock the user out: if we cannot read the
  // portfolio we cannot tell whether they hold the symbol, and refusing to
  // submit would be us enforcing an invariant we have no evidence about.
  const freeText = !!holdingsError;

  // Reopening for a different row has to re-seed the field; the dialog is kept
  // mounted by its parent, so state does not reset on its own.
  useEffect(() => {
    if (open) setSymbol(initialSymbol ?? '');
  }, [open, initialSymbol]);

  const handleSubmit = () => {
    mutation.mutate(
      {
        symbol: symbol.toUpperCase().trim(),
        template_name: templateName,
        params: {
          history_window_days: parseInt(historyDays),
          n_iterations: parseInt(iterations),
          alpha: parseFloat(alpha),
          min_H: parseFloat(minH),
          max_H: parseFloat(maxH),
          max_s0: parseInt(maxS0),
        },
      },
      {
        onSuccess: (data: any) => {
          onClose();
          if (onCreated && data.instance_id) onCreated(data.instance_id);
        },
      },
    );
  };

  const noHoldings = !freeText && !holdingsPending && symbols.length === 0;

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Create Harvest Plan</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {noHoldings ? (
            <Alert severity="info">
              No open positions — a harvest plan needs shares to sell.
            </Alert>
          ) : (
            <TextField
              label="Symbol"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              select={!freeText}
              fullWidth
              required
              disabled={holdingsPending && !freeText}
              placeholder={freeText ? 'e.g. GOOGL' : undefined}
              helperText={
                freeText
                  ? "Couldn't read your portfolio — the server still checks you hold the symbol."
                  : 'Symbols you hold an open position in.'
              }
            >
              {!freeText &&
                symbols.map((s) => (
                  <MenuItem key={s} value={s}>
                    {s}
                  </MenuItem>
                ))}
            </TextField>
          )}
          <TextField
            label="Template Name"
            value={templateName}
            onChange={(e) => setTemplateName(e.target.value)}
            fullWidth
          />
          <Accordion>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
              <Typography>Advanced Parameters</Typography>
            </AccordionSummary>
            <AccordionDetails>
              <Stack spacing={2}>
                <TextField label="History Window (days)" type="number" value={historyDays} onChange={(e) => setHistoryDays(e.target.value)} fullWidth />
                <TextField label="Iterations" type="number" value={iterations} onChange={(e) => setIterations(e.target.value)} fullWidth />
                <TextField label="Alpha" type="number" value={alpha} onChange={(e) => setAlpha(e.target.value)} fullWidth inputProps={{ step: '0.1' }} />
                <TextField label="Min H" type="number" value={minH} onChange={(e) => setMinH(e.target.value)} fullWidth inputProps={{ step: '0.01' }} />
                <TextField label="Max H" type="number" value={maxH} onChange={(e) => setMaxH(e.target.value)} fullWidth inputProps={{ step: '0.01' }} />
                <TextField label="Max S0" type="number" value={maxS0} onChange={(e) => setMaxS0(e.target.value)} fullWidth />
              </Stack>
            </AccordionDetails>
          </Accordion>
        </Stack>
        {mutation.isError && (
          <Alert severity="error" sx={{ mt: 2 }}>
            {(mutation.error as Error).message}
          </Alert>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button onClick={handleSubmit} variant="contained" disabled={!symbol.trim() || mutation.isPending}>
          {mutation.isPending ? 'Creating...' : 'Create Plan'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
