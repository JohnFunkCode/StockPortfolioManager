import { useState } from 'react';
import {
  Dialog, DialogTitle, DialogContent, DialogActions, Button, TextField, Stack,
  Accordion, AccordionSummary, AccordionDetails, Typography,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import { useCreatePlan } from '../../hooks/usePlans';

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated?: (instanceId: number) => void;
}

export default function CreatePlanDialog({ open, onClose, onCreated }: Props) {
  const [symbol, setSymbol] = useState('');
  const [templateName, setTemplateName] = useState('Default Template');
  const [historyDays, setHistoryDays] = useState('360');
  const [iterations, setIterations] = useState('4');
  const [alpha, setAlpha] = useState('0.5');
  const [minH, setMinH] = useState('0.05');
  const [maxH, setMaxH] = useState('0.30');
  const [maxS0, setMaxS0] = useState('1000');
  const mutation = useCreatePlan();

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

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Create Harvest Plan</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField
            label="Symbol"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            fullWidth
            required
            placeholder="e.g. GOOGL"
          />
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
          <Typography color="error" sx={{ mt: 1 }}>
            {(mutation.error as Error).message}
          </Typography>
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
