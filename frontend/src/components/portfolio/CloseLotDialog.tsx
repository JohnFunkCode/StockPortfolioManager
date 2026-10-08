import { useEffect, useState } from 'react';
import {
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import { useCloseLot, useClosePreview } from '../../hooks/usePortfolio';
import { formatCurrency, formatShares } from '../../utils/formatting';
import type { Lot } from '../../api/portfolioTypes';
import LotNoteField from './LotNoteField';

interface Props {
  open: boolean;
  lot: Lot;
  onClose: () => void;
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function CloseLotDialog({ open, lot, onClose }: Props) {
  const [shares, setShares] = useState(String(lot.quantity ?? ''));
  const [salePrice, setSalePrice] = useState(lot.current_price != null ? String(lot.current_price) : '');
  const [saleTradeDate, setSaleTradeDate] = useState(today());
  const [error, setError] = useState('');
  const [note, setNote] = useState('');
  const [sameForAll, setSameForAll] = useState(true);
  const [lotNotes, setLotNotes] = useState<Record<number, string>>({});
  const mutation = useCloseLot();

  // Which lots the sale will draw from. A FIFO/LIFO/HIFO sale can span several,
  // and each lot may have been bought (and now be sold) for a different reason.
  const [debouncedShares, setDebouncedShares] = useState(shares);
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedShares(shares), 300);
    return () => clearTimeout(timer);
  }, [shares]);
  const preview = useClosePreview(lot.lot_id, parseFloat(debouncedShares) || 0);
  const allocations = preview.data?.allocations ?? [];
  const perLotNotes = allocations.length > 1 && !sameForAll;

  const handleSubmit = () => {
    const sharesNum = parseFloat(shares);
    const priceNum = parseFloat(salePrice);
    if (!sharesNum || sharesNum <= 0) {
      setError('Shares must be greater than zero.');
      return;
    }
    if (!priceNum || priceNum <= 0) {
      setError('Sale price must be greater than zero.');
      return;
    }
    setError('');

    // Never send both: per-lot notes only when the user chose them, else one default.
    const noteFields: { notes?: string; lot_notes?: Record<number, string> } = {};
    if (perLotNotes) {
      const entries = Object.entries(lotNotes)
        .map(([id, text]) => [Number(id), text.trim()] as const)
        .filter(([id, text]) => text && allocations.some((a) => a.lot_id === id));
      if (entries.length) noteFields.lot_notes = Object.fromEntries(entries);
    } else if (note.trim()) {
      noteFields.notes = note.trim();
    }

    mutation.mutate(
      {
        lotId: lot.lot_id,
        data: {
          shares: sharesNum,
          sale_price: priceNum,
          sale_trade_date: saleTradeDate,
          ...noteFields,
        },
      },
      { onSuccess: onClose },
    );
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Sell {lot.symbol}</DialogTitle>
      <DialogContent>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5 }}>
          Lot has {formatShares(lot.quantity)} shares @ {lot.purchase_price ?? '—'}. Selling more than
          this lot holds allocates from your other open {lot.symbol} lots (FIFO).
        </Typography>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField
            label="Shares to Sell"
            type="number"
            value={shares}
            onChange={(e) => setShares(e.target.value)}
            fullWidth
            required
            inputProps={{ step: 'any', min: 0 }}
          />
          <TextField
            label="Sale Price"
            type="number"
            value={salePrice}
            onChange={(e) => setSalePrice(e.target.value)}
            fullWidth
            required
            inputProps={{ step: 0.01, min: 0 }}
          />
          <TextField
            label="Sale Date"
            type="date"
            value={saleTradeDate}
            onChange={(e) => setSaleTradeDate(e.target.value)}
            fullWidth
            required
            InputLabelProps={{ shrink: true }}
          />
          {allocations.length > 1 && (
            <>
              <Typography variant="caption" color="text.secondary">
                This sale draws from {allocations.length} lots.
              </Typography>
              <FormControlLabel
                control={
                  <Checkbox
                    size="small"
                    checked={sameForAll}
                    onChange={(e) => setSameForAll(e.target.checked)}
                  />
                }
                label="Same note for all lots"
              />
            </>
          )}
          {perLotNotes ? (
            allocations.map((a) => (
              <LotNoteField
                key={a.lot_id}
                label={`Lot #${a.lot_id} · ${a.trade_date ?? '—'} · ${formatShares(a.shares)} sh @ ${formatCurrency(a.purchase_price)}`}
                value={lotNotes[a.lot_id] ?? ''}
                onChange={(text) => setLotNotes((n) => ({ ...n, [a.lot_id]: text }))}
              />
            ))
          ) : (
            <LotNoteField
              label="Reason for sale (optional)"
              value={note}
              onChange={setNote}
              placeholder="Why are you selling?"
            />
          )}
          {(error || mutation.isError) && (
            <Typography variant="caption" sx={{ color: '#ef4444' }}>
              {error || (mutation.error as Error)?.message}
            </Typography>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={mutation.isPending}>Cancel</Button>
        <Button onClick={handleSubmit} variant="contained" color="success" disabled={mutation.isPending}>
          {mutation.isPending ? 'Selling…' : 'Sell'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
