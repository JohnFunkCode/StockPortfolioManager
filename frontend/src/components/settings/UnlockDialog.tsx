/**
 * Unlock dialog (BYOK 5a): one passphrase opens every stored provider key.
 * Reused by the chat gating in Phase 6, so it lives free of Settings-page
 * assumptions.
 */
import { useEffect, useState } from 'react';
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Typography,
} from '@mui/material';

import { useKeyVault } from '../../vault/KeyVaultContext';

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function UnlockDialog({ open, onClose }: Props) {
  const [passphrase, setPassphrase] = useState('');
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const { unlock } = useKeyVault();

  useEffect(() => {
    if (open) {
      setPassphrase('');
      setError('');
      setPending(false);
    }
  }, [open]);

  const handleSubmit = async () => {
    if (!passphrase) {
      setError('Enter your vault passphrase.');
      return;
    }
    setError('');
    setPending(true);
    try {
      await unlock(passphrase);
      onClose();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Unlock Vault</DialogTitle>

      <DialogContent>
        <Stack spacing={2} sx={{ mt: 0.5 }}>
          <TextField
            label="Vault passphrase *"
            size="small"
            type="password"
            value={passphrase}
            onChange={(e) => setPassphrase(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void handleSubmit();
            }}
            autoComplete="current-password"
            fullWidth
            autoFocus
          />
          {error && (
            <Typography variant="caption" sx={{ color: '#ef4444' }}>
              {error}
            </Typography>
          )}
        </Stack>
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onClose} disabled={pending}>Cancel</Button>
        <Button
          variant="contained"
          onClick={handleSubmit}
          disabled={pending || !passphrase}
        >
          {pending ? 'Unlocking…' : 'Unlock'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
