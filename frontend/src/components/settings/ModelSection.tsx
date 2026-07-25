/**
 * Sidekick model section of the Settings page (issue #124): a dropdown for
 * the chat model, backed by GET/PUT /api/settings via ChatContext — the
 * single source of truth shared with the chat-header quick-switch (WP8), so
 * changing the model here is reflected immediately in the live chat.
 */
import { useState } from 'react';
import {
  Alert,
  FormControl,
  MenuItem,
  Paper,
  Select,
  Stack,
  Typography,
  type SelectChangeEvent,
} from '@mui/material';

import { useChat } from '../../chat/ChatContext';

export default function ModelSection() {
  const { models, selectedModel, setSelectedModel, modelSaveError } = useChat();
  const [saving, setSaving] = useState(false);

  const handleChange = async (event: SelectChangeEvent) => {
    setSaving(true);
    try {
      await setSelectedModel(event.target.value);
    } finally {
      setSaving(false);
    }
  };

  const selected = models.find((m) => m.id === selectedModel);

  return (
    <Paper sx={{ p: 3 }}>
      <Typography variant="h6" sx={{ mb: 1 }}>
        Sidekick Model
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Choose which Claude model powers the Sidekick chat.
      </Typography>

      <Stack spacing={1}>
        <FormControl size="small" sx={{ maxWidth: 320 }}>
          <Select value={selectedModel} onChange={(e) => void handleChange(e)} disabled={saving}>
            {models.map((m) => (
              <MenuItem key={m.id} value={m.id}>
                {m.name}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        {selected && (
          <Typography variant="body2" color="text.secondary">
            {selected.description}
          </Typography>
        )}
        {modelSaveError && <Alert severity="error">{modelSaveError}</Alert>}
      </Stack>
    </Paper>
  );
}
