/** Settings page (BYOK 5a): API-keys vault section + Sidekick model (issue #124). */
import { Stack, Typography } from '@mui/material';

import ApiKeysSection from './ApiKeysSection';
import ModelSection from './ModelSection';

export default function SettingsPage() {
  return (
    <Stack spacing={3}>
      <Typography variant="h5" sx={{ fontWeight: 600 }}>
        Settings
      </Typography>
      <ApiKeysSection />
      <ModelSection />
    </Stack>
  );
}
