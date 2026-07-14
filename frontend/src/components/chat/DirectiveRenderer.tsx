/**
 * Renders one validated LLM directive as a live registry component.
 * Invalid/unknown directives degrade to a visible fallback (never crash the
 * rail), and each rendered component is wrapped in its own error boundary so
 * one bad chart can't take down the conversation.
 */
import { Component, type ReactNode } from 'react';
import { Alert, Box, Typography } from '@mui/material';
import { useSecurities } from '../../hooks/useSecurities';
import {
  COMPONENT_REGISTRY,
  validateDirective,
  type RegistryEntry,
} from '../../chat/componentRegistry';
import { DirectiveInteractionProvider } from '../../chat/DirectiveInteractions';
import type { ChatDirective } from '../../chat/types';

class DirectiveErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return (
        <Alert severity="error" data-testid="directive-error">
          This component failed to render.
        </Alert>
      );
    }
    return this.props.children;
  }
}

/** "TICKER — Name" header so multi-ticker chats stay unambiguous. */
function DirectiveTitle({ ticker }: { ticker: string }) {
  const { data } = useSecurities();
  const name = data?.securities.find((s) => s.symbol === ticker)?.name;
  return (
    <Typography variant="subtitle2" data-testid="directive-title" sx={{ fontWeight: 700, mb: 0.5 }}>
      {ticker}
      {name && name !== ticker ? ` — ${name}` : ''}
    </Typography>
  );
}

interface Props {
  directive: ChatDirective;
  registry?: Record<string, RegistryEntry>;
}

export default function DirectiveRenderer({ directive, registry = COMPONENT_REGISTRY }: Props) {
  const verdict = validateDirective(directive, registry);
  if (!verdict.ok) {
    return (
      <Alert severity="warning" data-testid="directive-fallback" sx={{ my: 0.5 }}>
        Couldn&apos;t render component: {verdict.reason}
        <Box component="code" sx={{ display: 'block', fontSize: 11, mt: 0.5, opacity: 0.8 }}>
          {JSON.stringify(directive)}
        </Box>
      </Alert>
    );
  }
  const entry = registry[directive.component];
  const Registered = entry.component;
  const passProps: Record<string, unknown> = { ...directive.props };
  if (typeof passProps.ticker === 'string') {
    passProps.ticker = passProps.ticker.trim().toUpperCase();
  }
  return (
    <DirectiveErrorBoundary>
      <Box data-testid={`directive-${directive.component}`} sx={{ my: 1 }}>
        {entry.titled && typeof passProps.ticker === 'string' && (
          <DirectiveTitle ticker={passProps.ticker} />
        )}
        <DirectiveInteractionProvider directive={directive} props={passProps}>
          <Registered {...passProps} />
        </DirectiveInteractionProvider>
      </Box>
    </DirectiveErrorBoundary>
  );
}
