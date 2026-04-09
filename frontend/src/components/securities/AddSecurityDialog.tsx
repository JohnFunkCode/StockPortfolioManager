import { useState, useEffect } from 'react';
import {
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Tab,
  Tabs,
  TextField,
  Typography,
} from '@mui/material';
import AccountBalanceWalletIcon from '@mui/icons-material/AccountBalanceWallet';
import VisibilityIcon from '@mui/icons-material/Visibility';
import { useAddSecurity } from '../../hooks/useSecurities';

interface Props {
  open: boolean;
  onClose: () => void;
}

const CURRENCIES = ['USD', 'EUR', 'GBP', 'CAD', 'AUD', 'JPY', 'CHF', 'HKD', 'SEK', 'NOK'];

const EMPTY_FORM = {
  symbol: '',
  name: '',
  currency: 'USD',
  tagsInput: '',
  purchase_price: '',
  quantity: '',
  purchase_date: '',
};

export default function AddSecurityDialog({ open, onClose }: Props) {
  const [destination, setDestination] = useState<0 | 1>(0); // 0 = watchlist, 1 = portfolio
  const [form, setForm] = useState(EMPTY_FORM);
  const [tagChips, setTagChips] = useState<string[]>([]);
  const [error, setError] = useState('');

  const { watchlist, portfolio } = useAddSecurity();
  const isPending = watchlist.isPending || portfolio.isPending;

  // Reset form when dialog opens/closes
  useEffect(() => {
    if (open) {
      setForm(EMPTY_FORM);
      setTagChips([]);
      setError('');
      watchlist.reset();
      portfolio.reset();
    }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (field: keyof typeof EMPTY_FORM) => (
    e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
  ) => setForm((f) => ({ ...f, [field]: e.target.value }));

  const handleTagKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      const tag = form.tagsInput.trim().replace(/,$/, '');
      if (tag && !tagChips.includes(tag)) {
        setTagChips((c) => [...c, tag]);
      }
      setForm((f) => ({ ...f, tagsInput: '' }));
    }
  };

  const removeTag = (tag: string) => setTagChips((c) => c.filter((t) => t !== tag));

  const handleSubmit = async () => {
    const symbol = form.symbol.trim().toUpperCase();
    if (!symbol) {
      setError('Symbol is required.');
      return;
    }
    setError('');

    const base = {
      symbol,
      name: form.name.trim() || undefined,
      currency: form.currency,
    };

    try {
      if (destination === 0) {
        await watchlist.mutateAsync({
          ...base,
          tags: tagChips.length ? tagChips : undefined,
        });
      } else {
        await portfolio.mutateAsync({
          ...base,
          purchase_price: form.purchase_price ? parseFloat(form.purchase_price) : undefined,
          quantity: form.quantity ? parseInt(form.quantity, 10) : undefined,
          purchase_date: form.purchase_date || undefined,
        });
      }
      onClose();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
    }
  };

  const isWatchlist = destination === 0;

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Add Security</DialogTitle>

      <DialogContent>
        <Tabs
          value={destination}
          onChange={(_e, v) => setDestination(v as 0 | 1)}
          sx={{ mb: 2.5 }}
        >
          <Tab
            icon={<VisibilityIcon fontSize="small" />}
            iconPosition="start"
            label="Watchlist"
          />
          <Tab
            icon={<AccountBalanceWalletIcon fontSize="small" />}
            iconPosition="start"
            label="Portfolio"
          />
        </Tabs>

        <Stack spacing={2}>
          {/* Symbol + Name */}
          <Stack direction="row" spacing={2}>
            <TextField
              label="Symbol *"
              size="small"
              value={form.symbol}
              onChange={set('symbol')}
              onBlur={() => setForm((f) => ({ ...f, symbol: f.symbol.trim().toUpperCase() }))}
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
            />
          </Stack>

          {/* Currency */}
          <FormControl size="small" sx={{ width: 120 }}>
            <InputLabel>Currency</InputLabel>
            <Select
              value={form.currency}
              label="Currency"
              onChange={(e) => setForm((f) => ({ ...f, currency: e.target.value }))}
            >
              {CURRENCIES.map((c) => (
                <MenuItem key={c} value={c}>{c}</MenuItem>
              ))}
            </Select>
          </FormControl>

          {/* Watchlist-only: tags */}
          {isWatchlist && (
            <Box>
              <TextField
                label="Tags (press Enter or comma to add)"
                size="small"
                fullWidth
                value={form.tagsInput}
                onChange={set('tagsInput')}
                onKeyDown={handleTagKey}
                placeholder="e.g. AI Software"
              />
              {tagChips.length > 0 && (
                <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap sx={{ mt: 1 }}>
                  {tagChips.map((t) => (
                    <Chip
                      key={t}
                      label={t}
                      size="small"
                      onDelete={() => removeTag(t)}
                    />
                  ))}
                </Stack>
              )}
            </Box>
          )}

          {/* Portfolio-only: price, qty, date */}
          {!isWatchlist && (
            <Stack spacing={2}>
              <Stack direction="row" spacing={2}>
                <TextField
                  label="Purchase Price"
                  size="small"
                  type="number"
                  value={form.purchase_price}
                  onChange={set('purchase_price')}
                  inputProps={{ min: 0, step: 0.01 }}
                  sx={{ flex: 1 }}
                />
                <TextField
                  label="Quantity"
                  size="small"
                  type="number"
                  value={form.quantity}
                  onChange={set('quantity')}
                  inputProps={{ min: 1, step: 1 }}
                  sx={{ flex: 1 }}
                />
              </Stack>
              <TextField
                label="Purchase Date"
                size="small"
                type="date"
                value={form.purchase_date}
                onChange={set('purchase_date')}
                InputLabelProps={{ shrink: true }}
                sx={{ width: 200 }}
              />
            </Stack>
          )}

          {error && (
            <Typography variant="caption" sx={{ color: '#ef4444' }}>
              {error}
            </Typography>
          )}
        </Stack>
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onClose} disabled={isPending}>Cancel</Button>
        <Button
          variant="contained"
          onClick={handleSubmit}
          disabled={isPending || !form.symbol.trim()}
        >
          {isPending ? 'Saving…' : `Add to ${isWatchlist ? 'Watchlist' : 'Portfolio'}`}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
