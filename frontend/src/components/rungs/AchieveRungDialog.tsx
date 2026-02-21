import { useState } from 'react';
import { Dialog, DialogTitle, DialogContent, DialogActions, Button, TextField } from '@mui/material';
import { useAchieveRung } from '../../hooks/useRungs';

interface Props {
  open: boolean;
  rungId: number;
  targetPrice: number;
  onClose: () => void;
}

export default function AchieveRungDialog({ open, rungId, targetPrice, onClose }: Props) {
  const [triggerPrice, setTriggerPrice] = useState(targetPrice.toFixed(2));
  const mutation = useAchieveRung();

  const handleSubmit = () => {
    mutation.mutate(
      { rungId, triggerPrice: parseFloat(triggerPrice) },
      { onSuccess: onClose },
    );
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Mark Rung Achieved</DialogTitle>
      <DialogContent>
        <TextField
          label="Trigger Price"
          type="number"
          value={triggerPrice}
          onChange={(e) => setTriggerPrice(e.target.value)}
          fullWidth
          margin="normal"
          inputProps={{ step: '0.01' }}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button onClick={handleSubmit} variant="contained" disabled={mutation.isPending}>
          Mark Achieved
        </Button>
      </DialogActions>
    </Dialog>
  );
}
