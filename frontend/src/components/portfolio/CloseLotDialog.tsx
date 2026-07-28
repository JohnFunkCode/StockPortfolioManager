import { useState } from 'react';
import { Dialog, DialogTitle, DialogContent, DialogActions, Button, TextField, Stack, Typography } from '@mui/material';
import { useCloseLot } from '../../hooks/usePortfolio';
import { formatShares } from '../../utils/formatting';
import type { Lot } from '../../api/portfolioTypes';

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
  const mutation = useCloseLot();

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
    mutation.mutate(
      {
        lotId: lot.lot_id,
        data: {
          shares: sharesNum,
          sale_price: priceNum,
          sale_trade_date: saleTradeDate,
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
