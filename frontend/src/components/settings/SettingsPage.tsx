/**
 * Settings page (BYOK 5a): API-keys vault section, Sidekick model (issue #124)
 * and the daily Sidekick token budget (G2, #461).
 */
import { Stack, Typography } from '@mui/material';

import ApiKeysSection from './ApiKeysSection';
import BudgetSection from './BudgetSection';
import ModelSection from './ModelSection';

export default function SettingsPage() {
  return (
    <Stack spacing={3}>
      <Typography variant="h5" sx={{ fontWeight: 600 }}>
        Settings
      </Typography>
      <ApiKeysSection />
      <ModelSection />
      <BudgetSection />
    </Stack>
  );
}
