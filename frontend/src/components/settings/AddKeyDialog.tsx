/**
 * Add-key dialog (BYOK 5a), modeled on securities/AddSecurityDialog.
 *
 * Flow: paste key + passphrase → seal-and-validate through the key proxy →
 * store encrypted in the vault only on success (failure keeps the dialog
 * open with the error). When this is the vault's first key the passphrase is
 * being *set*, so the strength minimum and a confirm field apply; afterwards
 * the one-passphrase rule means the existing vault passphrase is required.
 */
import { useEffect, useState } from 'react';
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from '@mui/material';

import { useKeyVault } from '../../vault/KeyVaultContext';
import { useValidateKey } from '../../hooks/useKeyProxy';
import { estimatePassphraseStrength } from './passphraseStrength';

export const SUPPORTED_PROVIDERS = [{ id: 'anthropic', name: 'Anthropic' }];

interface Props {
  open: boolean;
  onClose: () => void;
}

const EMPTY_FORM = {
  provider: 'anthropic',
  label: '',
  apiKey: '',
  passphrase: '',
  confirm: '',
};

export default function AddKeyDialog({ open, onClose }: Props) {
  const [form, setForm] = useState(EMPTY_FORM);
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const { keys, addKey } = useKeyVault();
  const validate = useValidateKey();

  // First key = the passphrase is being set; afterwards it must match the vault's.
  const creatingVault = keys.length === 0;
  const strength = estimatePassphraseStrength(form.passphrase);

  useEffect(() => {
    if (open) {
      setForm(EMPTY_FORM);
      setError('');
      setPending(false);
      validate.reset();
    }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (field: keyof typeof EMPTY_FORM) => (
    e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
  ) => setForm((f) => ({ ...f, [field]: e.target.value }));

  const handleSubmit = async () => {
    const apiKey = form.apiKey.trim();
    if (!apiKey) {
      setError('Paste an API key.');
      return;
    }
    if (!form.passphrase) {
      setError('A passphrase is required.');
      return;
    }
    if (creatingVault) {
      if (!strength.ok) {
        setError(strength.feedback);
        return;
      }
      if (form.passphrase !== form.confirm) {
        setError('Passphrases do not match.');
        return;
      }
    }
    setError('');
    setPending(true);
    try {
      await validate.mutateAsync({ provider: form.provider, apiKey });
      await addKey({
        provider: form.provider,
        apiKey,
        passphrase: form.passphrase,
        label: form.label.trim() || 'Personal key',
      });
      onClose();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Add API Key</DialogTitle>

      <DialogContent>
        <Stack spacing={2} sx={{ mt: 0.5 }}>
          <Typography variant="body2" color="text.secondary">
            Your key is validated once through the key proxy, then stored encrypted
            in this browser only — clearing site data or switching devices means
            pasting it again.
          </Typography>

          <Stack direction="row" spacing={2}>
            <FormControl size="small" sx={{ width: 160 }}>
              <InputLabel id="add-key-provider-label">Provider</InputLabel>
              <Select
                labelId="add-key-provider-label"
                value={form.provider}
                label="Provider"
                onChange={(e) => setForm((f) => ({ ...f, provider: e.target.value }))}
              >
                {SUPPORTED_PROVIDERS.map((p) => (
                  <MenuItem key={p.id} value={p.id}>{p.name}</MenuItem>
                ))}
              </Select>
            </FormControl>
            <TextField
              label="Label"
              size="small"
              value={form.label}
              onChange={set('label')}
              placeholder="Personal key"
              sx={{ flex: 1 }}
            />
          </Stack>

          <TextField
            label="API key *"
            size="small"
            type="password"
            value={form.apiKey}
            onChange={set('apiKey')}
            autoComplete="off"
            fullWidth
            autoFocus
          />

          <TextField
            label={creatingVault ? 'Vault passphrase *' : 'Existing vault passphrase *'}
            size="small"
            type="password"
            value={form.passphrase}
            onChange={set('passphrase')}
            autoComplete="new-password"
            fullWidth
            helperText={
              creatingVault
                ? form.passphrase
                  ? strength.feedback
                  : 'Protects every key in this vault. You will need it to unlock.'
                : 'One passphrase covers every key in this vault.'
            }
            FormHelperTextProps={{
              sx: creatingVault && form.passphrase && !strength.ok
                ? { color: 'warning.main' }
                : undefined,
            }}
          />

          {creatingVault && (
            <TextField
              label="Confirm passphrase *"
              size="small"
              type="password"
              value={form.confirm}
              onChange={set('confirm')}
              autoComplete="new-password"
              fullWidth
            />
          )}

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
          disabled={pending || !form.apiKey.trim() || !form.passphrase}
        >
          {pending ? 'Validating…' : 'Validate & Save'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
