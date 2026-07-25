/**
 * Sidekick model section of the Settings page (issue #124): a dropdown for
 * the chat model, backed by GET/PUT /api/settings. The canonical surface for
 * changing the model — the chat-header quick-switch (WP8) writes through the
 * same endpoint and stays in sync.
 */
import { useEffect, useState } from 'react';
import {
  CircularProgress,
  FormControl,
  MenuItem,
  Paper,
  Select,
  Stack,
  Typography,
  type SelectChangeEvent,
} from '@mui/material';

import { getSettings, putChatModel } from '../../api/settings';
import { CHAT_MODELS, DEFAULT_CHAT_MODEL, type ChatModel } from '../../chat/models';

export default function ModelSection() {
  const [models, setModels] = useState<ChatModel[]>(CHAT_MODELS);
  const [chatModel, setChatModel] = useState(DEFAULT_CHAT_MODEL);
  const [ready, setReady] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getSettings()
      .then((view) => {
        if (cancelled) return;
        setModels(view.models);
        setChatModel(view.chat_model);
      })
      .finally(() => {
        if (!cancelled) setReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleChange = async (event: SelectChangeEvent) => {
    const id = event.target.value;
    setChatModel(id);
    setSaving(true);
    try {
      await putChatModel(id);
    } finally {
      setSaving(false);
    }
  };

  const selected = models.find((m) => m.id === chatModel);

  if (!ready) {
    return (
      <Paper sx={{ p: 3, display: 'flex', justifyContent: 'center' }}>
        <CircularProgress size={24} />
      </Paper>
    );
  }

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
          <Select value={chatModel} onChange={(e) => void handleChange(e)} disabled={saving}>
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
      </Stack>
    </Paper>
  );
}
