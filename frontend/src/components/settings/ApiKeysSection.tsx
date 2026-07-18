/**
 * API-keys section of the Settings page (BYOK 5a): one row per supported
 * provider showing label, masked last4, and lock status, with Add / Rotate /
 * Remove actions plus vault-level Unlock and Lock.
 */
import { useState } from 'react';
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Paper,
  Stack,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import LockIcon from '@mui/icons-material/Lock';
import LockOpenIcon from '@mui/icons-material/LockOpen';

import { useKeyVault } from '../../vault/KeyVaultContext';
import ConfirmDialog from '../common/ConfirmDialog';
import AddKeyDialog, { SUPPORTED_PROVIDERS } from './AddKeyDialog';
import RotateKeyDialog from './RotateKeyDialog';
import UnlockDialog from './UnlockDialog';

export default function ApiKeysSection() {
  const { keys, ready, status, removeKey, lock } = useKeyVault();
  const [addOpen, setAddOpen] = useState(false);
  const [unlockOpen, setUnlockOpen] = useState(false);
  const [rotateProvider, setRotateProvider] = useState<string | null>(null);
  const [removeProvider, setRemoveProvider] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);

  if (!ready) {
    return (
      <Paper sx={{ p: 3, display: 'flex', justifyContent: 'center' }}>
        <CircularProgress size={24} />
      </Paper>
    );
  }

  const anyLocked = keys.some((k) => status(k.provider) === 'locked');
  const anyUnlocked = keys.some((k) => status(k.provider) === 'unlocked');

  const handleRemove = async () => {
    if (removeProvider === null) return;
    setRemoving(true);
    try {
      await removeKey(removeProvider);
    } finally {
      setRemoving(false);
      setRemoveProvider(null);
    }
  };

  const removeName =
    SUPPORTED_PROVIDERS.find((p) => p.id === removeProvider)?.name ?? removeProvider;

  return (
    <Paper sx={{ p: 3 }}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 1 }}>
        <Typography variant="h6">LLM API Keys</Typography>
        <Stack direction="row" spacing={1}>
          {anyLocked && (
            <Button
              size="small"
              startIcon={<LockOpenIcon />}
              onClick={() => setUnlockOpen(true)}
            >
              Unlock
            </Button>
          )}
          {anyUnlocked && (
            <Button size="small" startIcon={<LockIcon />} onClick={() => lock()}>
              Lock
            </Button>
          )}
        </Stack>
      </Stack>

      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Keys are encrypted with your vault passphrase and stored in this browser
        only. The server sees them solely inside sealed, single-use envelopes.
      </Typography>

      <Stack spacing={1.5}>
        {SUPPORTED_PROVIDERS.map((p) => {
          const stored = keys.find((k) => k.provider === p.id);
          const providerStatus = status(p.id);
          return (
            <Box
              key={p.id}
              sx={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                border: 1,
                borderColor: 'divider',
                borderRadius: 1,
                px: 2,
                py: 1.5,
              }}
            >
              <Stack direction="row" spacing={2} alignItems="center">
                <Typography sx={{ fontWeight: 600, minWidth: 90 }}>{p.name}</Typography>
                {stored ? (
                  <>
                    <Typography variant="body2" color="text.secondary">
                      {stored.label}
                    </Typography>
                    <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                      ••••{stored.last4}
                    </Typography>
                    <Chip
                      size="small"
                      label={providerStatus === 'unlocked' ? 'Unlocked' : 'Locked'}
                      color={providerStatus === 'unlocked' ? 'success' : 'default'}
                    />
                  </>
                ) : (
                  <Typography variant="body2" color="text.secondary">
                    No key stored
                  </Typography>
                )}
              </Stack>

              <Stack direction="row" spacing={1}>
                {stored ? (
                  <>
                    <Button size="small" onClick={() => setRotateProvider(p.id)}>
                      Rotate
                    </Button>
                    <Button
                      size="small"
                      color="error"
                      onClick={() => setRemoveProvider(p.id)}
                    >
                      Remove
                    </Button>
                  </>
                ) : (
                  <Button
                    size="small"
                    variant="contained"
                    startIcon={<AddIcon />}
                    onClick={() => setAddOpen(true)}
                  >
                    Add Key
                  </Button>
                )}
              </Stack>
            </Box>
          );
        })}
      </Stack>

      <AddKeyDialog open={addOpen} onClose={() => setAddOpen(false)} />
      <UnlockDialog open={unlockOpen} onClose={() => setUnlockOpen(false)} />
      <RotateKeyDialog
        open={rotateProvider !== null}
        onClose={() => setRotateProvider(null)}
        provider={rotateProvider ?? ''}
      />
      <ConfirmDialog
        open={removeProvider !== null}
        title="Remove API Key"
        message={`Remove the stored ${removeName} key from this browser? You can add it again later.`}
        confirmLabel="Remove"
        loading={removing}
        onConfirm={() => void handleRemove()}
        onCancel={() => setRemoveProvider(null)}
      />
    </Paper>
  );
}
