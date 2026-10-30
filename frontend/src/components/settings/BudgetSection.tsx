/**
 * Sidekick daily budget section of the Settings page (G2, #461): today's token
 * use against the budget, and a field to lower the user's own cap below the
 * system limit. The server owns every figure (the effective budget included)
 * and validates the cap; this only shows them and sends the user's choice.
 */
import { useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  LinearProgress,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material';

import { getSettings, putChatBudget, type SettingsView } from '../../api/settings';

const fmt = (n: number) => n.toLocaleString('en-US');

export default function BudgetSection() {
  const [view, setView] = useState<SettingsView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    getSettings()
      .then((v) => {
        if (!live) return;
        setView(v);
        setDraft(v.chat_daily_token_budget == null ? '' : String(v.chat_daily_token_budget));
      })
      .catch((e: unknown) => {
        if (live) setLoadError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      live = false;
    };
  }, []);

  const save = async (budget: number | null) => {
    setSaving(true);
    setSaveError(null);
    try {
      const v = await putChatBudget(budget);
      setView(v);
      setDraft(v.chat_daily_token_budget == null ? '' : String(v.chat_daily_token_budget));
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const header = (
    <>
      <Typography variant="h6" sx={{ mb: 1 }}>
        Sidekick Daily Budget
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Tokens Sidekick may use for you each day. The count resets at midnight US Eastern.
      </Typography>
    </>
  );

  if (loadError) {
    return (
      <Paper sx={{ p: 3 }}>
        {header}
        <Alert severity="error">{loadError}</Alert>
      </Paper>
    );
  }
  if (!view) {
    return (
      <Paper sx={{ p: 3 }}>
        {header}
        <CircularProgress size={20} aria-label="Loading budget" />
      </Paper>
    );
  }

  const effective = view.chat_daily_token_budget_effective;
  const used = view.chat_tokens_used_today;
  const ceiling = view.chat_daily_token_budget_ceiling;
  const parsed = draft.trim() === '' ? null : Number(draft);
  const draftValid = parsed !== null && Number.isInteger(parsed) && parsed >= 1 && parsed <= ceiling;
  const unchanged = parsed === view.chat_daily_token_budget;

  return (
    <Paper sx={{ p: 3 }}>
      {header}
      <Stack spacing={2}>
        <Box>
          <Typography variant="body2" sx={{ mb: 0.5 }}>
            Used today: {fmt(used)} of {fmt(effective)} tokens
          </Typography>
          <LinearProgress
            variant="determinate"
            value={effective > 0 ? Math.min(100, (100 * used) / effective) : 0}
            aria-label="Budget used today"
          />
        </Box>

        <Stack direction="row" spacing={1} alignItems="flex-start">
          <TextField
            size="small"
            type="number"
            label="Your daily limit (tokens)"
            placeholder={`System limit: ${fmt(ceiling)}`}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            disabled={saving}
            error={draft.trim() !== '' && !draftValid}
            helperText={
              draft.trim() !== '' && !draftValid
                ? `Enter a whole number from 1 to ${fmt(ceiling)}.`
                : `Lower it below the system limit of ${fmt(ceiling)} if you like.`
            }
            inputProps={{ min: 1, max: ceiling, step: 1 }}
            sx={{ maxWidth: 320 }}
          />
          <Button
            variant="contained"
            disabled={saving || !draftValid || unchanged}
            onClick={() => void save(parsed)}
          >
            Save
          </Button>
          {view.chat_daily_token_budget != null && (
            <Button disabled={saving} onClick={() => void save(null)}>
              Use system limit
            </Button>
          )}
        </Stack>

        {saveError && <Alert severity="error">{saveError}</Alert>}
        <Typography variant="body2" color="text.secondary">
          Need more than the system limit? Please contact the development team.
        </Typography>
      </Stack>
    </Paper>
  );
}
