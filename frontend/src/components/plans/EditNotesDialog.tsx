import { useState } from 'react';
import { Dialog, DialogTitle, DialogContent, DialogActions, Button, TextField } from '@mui/material';
import { useUpdatePlan } from '../../hooks/usePlans';

interface Props {
  open: boolean;
  planId: number;
  currentNotes: string | null;
  onClose: () => void;
}

export default function EditNotesDialog({ open, planId, currentNotes, onClose }: Props) {
  const [notes, setNotes] = useState(currentNotes ?? '');
  const mutation = useUpdatePlan();

  const handleSubmit = () => {
    mutation.mutate({ id: planId, data: { notes } }, { onSuccess: onClose });
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Edit Notes</DialogTitle>
      <DialogContent>
        <TextField
          label="Notes"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          fullWidth
          multiline
          rows={4}
          margin="normal"
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button onClick={handleSubmit} variant="contained" disabled={mutation.isPending}>
          Save
        </Button>
      </DialogActions>
    </Dialog>
  );
}
