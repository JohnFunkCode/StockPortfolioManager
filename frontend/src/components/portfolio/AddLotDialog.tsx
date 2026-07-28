import { useEffect, useState } from 'react';
import {
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import { useSymbolLookup } from '../../hooks/useSecurities';
import { useCreateLot } from '../../hooks/usePortfolio';

interface Props {
  open: boolean;
  onClose: () => void;
}

const EMPTY_FORM = {
  symbol: '',
  name: '',
  purchase_price: '',
  quantity: '',
  trade_date: '',
};

export default function AddLotDialog({ open, onClose }: Props) {
  const [form, setForm] = useState(EMPTY_FORM);
  const [error, setError] = useState('');
  const [lookupSymbol, setLookupSymbol] = useState('');

  const createLot = useCreateLot();
  const { data: lookupData, isFetching: isLookingUp } = useSymbolLookup(lookupSymbol);

  useEffect(() => {
    if (!lookupData) return;
    if (!form.name && lookupData.name) {
      setForm((f) => ({ ...f, name: lookupData.name }));
    }
  }, [lookupData]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (open) {
      setForm(EMPTY_FORM);
      setError('');
      setLookupSymbol('');
      createLot.reset();
    }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (field: keyof typeof EMPTY_FORM) => (
    e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
  ) => setForm((f) => ({ ...f, [field]: e.target.value }));

  const handleSubmit = async () => {
    const symbol = form.symbol.trim().toUpperCase();
    if (!symbol) {
      setError('Symbol is required.');
      return;
    }
    if (!form.purchase_price || !form.quantity || !form.trade_date) {
      setError('Purchase price, quantity, and trade date are required.');
      return;
    }
    setError('');

    try {
      await createLot.mutateAsync({
        symbol,
        name: form.name.trim() || undefined,
        purchase_price: parseFloat(form.purchase_price),
        quantity: parseFloat(form.quantity),
        trade_date: form.trade_date,
      });
      onClose();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Add Lot</DialogTitle>

      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <Stack direction="row" spacing={2}>
            <TextField
              label="Symbol *"
              size="small"
              value={form.symbol}
              onChange={set('symbol')}
              onBlur={() => {
                const sym = form.symbol.trim().toUpperCase();
                setForm((f) => ({ ...f, symbol: sym }));
                if (sym) setLookupSymbol(sym);
              }}
              inputProps={{ style: { textTransform: 'uppercase' } }}
              sx={{ width: 120 }}
              autoFocus
            />
            <TextField
              label="Name"
              size="small"
              value={form.name}
              onChange={set('name')}
              placeholder="e.g. Apple Inc."
              sx={{ flex: 1 }}
              InputProps={isLookingUp ? {
                endAdornment: <CircularProgress size={14} sx={{ mr: 0.5 }} />,
              } : undefined}
            />
          </Stack>

          <Stack direction="row" spacing={2}>
            <TextField
              label="Purchase Price *"
              size="small"
              type="number"
              value={form.purchase_price}
              onChange={set('purchase_price')}
              inputProps={{ min: 0, step: 0.01 }}
              sx={{ flex: 1 }}
            />
            <TextField
              label="Quantity *"
              size="small"
              type="number"
              value={form.quantity}
              onChange={set('quantity')}
              inputProps={{ min: 0, step: 'any' }}
              sx={{ flex: 1 }}
            />
          </Stack>
          <TextField
            label="Trade Date *"
            size="small"
            type="date"
            value={form.trade_date}
            onChange={set('trade_date')}
            InputLabelProps={{ shrink: true }}
            sx={{ width: 200 }}
          />

          {error && (
            <Typography variant="caption" sx={{ color: '#ef4444' }}>
              {error}
            </Typography>
          )}
        </Stack>
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onClose} disabled={createLot.isPending}>Cancel</Button>
        <Button
          variant="contained"
          onClick={handleSubmit}
          disabled={createLot.isPending || !form.symbol.trim()}
        >
          {createLot.isPending ? 'Saving…' : 'Add Lot'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
