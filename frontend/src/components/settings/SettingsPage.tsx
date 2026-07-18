/** Settings page (BYOK 5a): currently just the API-keys vault section. */
import { Stack, Typography } from '@mui/material';

import ApiKeysSection from './ApiKeysSection';

export default function SettingsPage() {
  return (
    <Stack spacing={3}>
      <Typography variant="h5" sx={{ fontWeight: 600 }}>
        Settings
      </Typography>
      <ApiKeysSection />
    </Stack>
  );
}
