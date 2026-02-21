import { useState } from 'react';
import { Dialog, DialogTitle, DialogContent, DialogActions, Button, TextField, Stack } from '@mui/material';
import { useExecuteRung } from '../../hooks/useRungs';

interface Props {
  open: boolean;
  rungId: number;
  sharesSoldPlanned: number;
  targetPrice: number;
  onClose: () => void;
}

export default function ExecuteRungDialog({ open, rungId, sharesSoldPlanned, targetPrice, onClose }: Props) {
  const [executedPrice, setExecutedPrice] = useState(targetPrice.toFixed(2));
  const [sharesSold, setSharesSold] = useState(String(sharesSoldPlanned));
  const [taxPaid, setTaxPaid] = useState('0');
  const [notes, setNotes] = useState('');
  const mutation = useExecuteRung();

  const handleSubmit = () => {
    mutation.mutate(
      {
        rungId,
        data: {
          executed_price: parseFloat(executedPrice),
          shares_sold: parseInt(sharesSold),
          tax_paid: parseFloat(taxPaid) || undefined,
          notes: notes || undefined,
        },
      },
      { onSuccess: onClose },
    );
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Record Execution</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField
            label="Executed Price"
            type="number"
            value={executedPrice}
            onChange={(e) => setExecutedPrice(e.target.value)}
            fullWidth
            required
            inputProps={{ step: '0.01' }}
          />
          <TextField
            label="Shares Sold"
            type="number"
            value={sharesSold}
            onChange={(e) => setSharesSold(e.target.value)}
            fullWidth
            required
          />
          <TextField
            label="Tax Paid"
            type="number"
            value={taxPaid}
            onChange={(e) => setTaxPaid(e.target.value)}
            fullWidth
            inputProps={{ step: '0.01' }}
          />
          <TextField
            label="Notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            fullWidth
            multiline
            rows={2}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button onClick={handleSubmit} variant="contained" color="success" disabled={mutation.isPending}>
          Record Execution
        </Button>
      </DialogActions>
    </Dialog>
  );
}
