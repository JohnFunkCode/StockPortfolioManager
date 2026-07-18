/**
 * Rotate-key dialog (BYOK 5a): replace one provider's key material under the
 * existing vault passphrase. The new key is validated through the key proxy
 * before the old record is overwritten — a failed validation (or a wrong
 * passphrase) leaves the stored key untouched and the dialog open.
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
import { useValidateKey } from '../../hooks/useKeyProxy';

interface Props {
  open: boolean;
  onClose: () => void;
  provider: string;
}

export default function RotateKeyDialog({ open, onClose, provider }: Props) {
  const [apiKey, setApiKey] = useState('');
  const [passphrase, setPassphrase] = useState('');
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const { rotateKey } = useKeyVault();
  const validate = useValidateKey();

  useEffect(() => {
    if (open) {
      setApiKey('');
      setPassphrase('');
      setError('');
      setPending(false);
      validate.reset();
    }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSubmit = async () => {
    const key = apiKey.trim();
    if (!key) {
      setError('Paste the new API key.');
      return;
    }
    if (!passphrase) {
      setError('Your vault passphrase is required.');
      return;
    }
    setError('');
    setPending(true);
    try {
      await validate.mutateAsync({ provider, apiKey: key });
      await rotateKey({ provider, apiKey: key, passphrase });
      onClose();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Rotate API Key</DialogTitle>

      <DialogContent>
        <Stack spacing={2} sx={{ mt: 0.5 }}>
          <Typography variant="body2" color="text.secondary">
            The new key is validated before the old one is replaced.
          </Typography>
          <TextField
            label="New API key *"
            size="small"
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            autoComplete="off"
            fullWidth
            autoFocus
          />
          <TextField
            label="Vault passphrase *"
            size="small"
            type="password"
            value={passphrase}
            onChange={(e) => setPassphrase(e.target.value)}
            autoComplete="current-password"
            fullWidth
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
          disabled={pending || !apiKey.trim() || !passphrase}
        >
          {pending ? 'Validating…' : 'Rotate Key'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
