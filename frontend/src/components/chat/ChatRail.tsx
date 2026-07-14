/**
 * The sidekick rail: message list (text, inline directive components, tool
 * chips), input row, error surface. Mounted in Layout so it persists across
 * routes; visibility is controlled by ChatContext.railOpen.
 */
import { useEffect, useRef, useState } from 'react';
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  IconButton,
  Paper,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import SendIcon from '@mui/icons-material/Send';
import DeleteSweepIcon from '@mui/icons-material/DeleteSweep';
import BoltIcon from '@mui/icons-material/Bolt';
import FullscreenIcon from '@mui/icons-material/Fullscreen';
import FullscreenExitIcon from '@mui/icons-material/FullscreenExit';
import { useChat } from '../../chat/ChatContext';
import type { ChatMessage, Segment } from '../../chat/types';
import DirectiveRenderer from './DirectiveRenderer';

const RAIL_WIDTH = 400;

function SegmentView({ segment }: { segment: Segment }) {
  if (segment.type === 'text') {
    return (
      <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
        {segment.text}
      </Typography>
    );
  }
  if (segment.type === 'directive') {
    return <DirectiveRenderer directive={segment.directive} />;
  }
  return (
    <Chip
      size="small"
      icon={segment.state === 'running' ? <CircularProgress size={12} /> : <BoltIcon />}
      label={segment.tool}
      color={segment.state === 'error' ? 'warning' : 'default'}
      variant="outlined"
      data-testid={`tool-chip-${segment.tool}`}
      data-state={segment.state}
      sx={{ my: 0.25, fontSize: 11 }}
    />
  );
}

function MessageView({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user';
  // Bubbles shrink to fit text, but rendered components (charts, panels)
  // should span the rail — go full width once a directive is present.
  const hasDirective = message.segments.some((s) => s.type === 'directive');
  return (
    <Box
      data-testid={`chat-message-${message.role}`}
      sx={{ display: 'flex', justifyContent: isUser ? 'flex-end' : 'flex-start' }}
    >
      <Paper
        elevation={0}
        data-fullwidth={hasDirective ? 'true' : undefined}
        sx={{
          px: 1.5,
          py: 1,
          maxWidth: '95%',
          width: hasDirective ? '95%' : 'auto',
          bgcolor: isUser ? 'primary.dark' : 'background.paper',
          border: 1,
          borderColor: 'divider',
        }}
      >
        {message.segments.map((segment, i) => (
          <SegmentView key={i} segment={segment} />
        ))}
        {message.segments.length === 0 && (
          <Typography variant="body2" color="text.secondary">
            …
          </Typography>
        )}
      </Paper>
    </Box>
  );
}

/** "select_strike: strike 120" — compact composer-chip label for a gesture. */
function interactionLabel(action: string, payload: Record<string, unknown>): string {
  const detail = Object.entries(payload)
    .map(([k, v]) => `${k} ${v}`)
    .join(', ');
  return `${action.replace(/_/g, ' ')}: ${detail}`;
}

export default function ChatRail() {
  const {
    messages,
    isStreaming,
    error,
    expanded,
    setExpanded,
    sendMessage,
    clearConversation,
    pendingInteractions,
    removeInteraction,
  } = useChat();
  const [draft, setDraft] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  // In fullscreen, give directive components (charts, signal panels) most of
  // the width while keeping comfortable side gutters.
  const centered = expanded ? { maxWidth: '80%', mx: 'auto', width: '100%' } : {};

  useEffect(() => {
    const el = scrollRef.current;
    if (el && typeof el.scrollTo === 'function') {
      el.scrollTo({ top: el.scrollHeight });
    }
  }, [messages, isStreaming]);

  const submit = () => {
    const text = draft.trim();
    if (!text || isStreaming) return;
    setDraft('');
    void sendMessage(text);
    // Scroll every scrollable ancestor (message list AND the page itself) to
    // the conversation bottom after the new message renders.
    requestAnimationFrame(() => {
      bottomRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'end' });
    });
  };

  return (
    <Box
      data-testid="chat-rail"
      data-expanded={expanded}
      sx={{
        ...(expanded ? { flex: 1, minWidth: 0 } : { width: RAIL_WIDTH, flexShrink: 0 }),
        display: 'flex',
        flexDirection: 'column',
        borderLeft: expanded ? 0 : 1,
        borderColor: 'divider',
        height: '100%',
        minHeight: 0,
      }}
    >
      <Stack direction="row" alignItems="center" sx={{ px: 1.5, py: 1, ...centered }} spacing={1}>
        <Typography variant="subtitle2" sx={{ flex: 1 }}>
          Sidekick
        </Typography>
        <Tooltip title={expanded ? 'Collapse to side rail' : 'Expand to full screen'}>
          <IconButton size="small" onClick={() => setExpanded(!expanded)} data-testid="chat-expand">
            {expanded ? <FullscreenExitIcon fontSize="small" /> : <FullscreenIcon fontSize="small" />}
          </IconButton>
        </Tooltip>
        <Tooltip title="Clear conversation">
          <IconButton size="small" onClick={clearConversation} data-testid="chat-clear">
            <DeleteSweepIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Stack>

      <Box
        ref={scrollRef}
        sx={{ flex: 1, minHeight: 0, overflowY: 'auto', px: 1.5, py: 1, display: 'flex', flexDirection: 'column', gap: 1, ...centered }}
      >
        {messages.length === 0 && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 2, textAlign: 'center' }}>
            Ask about any ticker — I can pull signals, prices, fundamentals and
            render live panels right here.
          </Typography>
        )}
        {messages.map((message, i) => (
          <MessageView key={i} message={message} />
        ))}
        <div ref={bottomRef} data-testid="chat-bottom-sentinel" />
      </Box>

      {error && (
        <Alert severity="error" data-testid="chat-error" sx={{ mx: 1.5, mb: 1, ...centered }}>
          {error}
        </Alert>
      )}

      {pendingInteractions.length > 0 && (
        <Stack
          direction="row"
          spacing={0.5}
          flexWrap="wrap"
          useFlexGap
          sx={{ px: 1.5, pb: 0.5, ...centered }}
        >
          {pendingInteractions.map((interaction, i) => (
            <Chip
              key={`${interaction.component_id}-${interaction.action}-${i}`}
              size="small"
              color="info"
              variant="outlined"
              data-testid="pending-interaction"
              label={interactionLabel(interaction.action, interaction.payload)}
              onDelete={() => removeInteraction(i)}
              sx={{ fontSize: 11 }}
            />
          ))}
        </Stack>
      )}

      <Stack direction="row" spacing={1} sx={{ p: 1.5, pt: 0.5, ...centered }}>
        <TextField
          data-testid="chat-input"
          size="small"
          fullWidth
          multiline
          maxRows={4}
          placeholder={isStreaming ? 'Thinking…' : 'Ask the sidekick…'}
          value={draft}
          disabled={isStreaming}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
        />
        <IconButton
          color="primary"
          onClick={submit}
          disabled={isStreaming || !draft.trim()}
          data-testid="chat-send"
        >
          <SendIcon />
        </IconButton>
      </Stack>
    </Box>
  );
}
