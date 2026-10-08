import { useState } from 'react';
import { Button, Dialog, DialogActions, DialogContent, DialogTitle, Typography } from '@mui/material';
import { useUpdateLot } from '../../hooks/usePortfolio';
import type { Lot } from '../../api/portfolioTypes';
import LotNoteField from './LotNoteField';

interface Props {
  open: boolean;
  lot: Lot;
  onClose: () => void;
}

/** Add, change, or clear the reason a lot was bought. An empty note clears it. */
export default function EditLotNoteDialog({ open, lot, onClose }: Props) {
  const [note, setNote] = useState(lot.notes ?? '');
  const mutation = useUpdateLot();

  const handleSave = () => {
    mutation.mutate({ lotId: lot.lot_id, data: { notes: note.trim() } }, { onSuccess: onClose });
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Reason for purchase — {lot.symbol}</DialogTitle>
      <DialogContent>
        <LotNoteField
          label="Reason for purchase"
          value={note}
          onChange={setNote}
          placeholder="Why did you buy this lot?"
        />
        {mutation.isError && (
          <Typography variant="caption" sx={{ color: '#ef4444', display: 'block', mt: 1 }}>
            {(mutation.error as Error).message}
          </Typography>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={mutation.isPending}>Cancel</Button>
        <Button onClick={handleSave} variant="contained" disabled={mutation.isPending}>
          {mutation.isPending ? 'Saving…' : 'Save'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
