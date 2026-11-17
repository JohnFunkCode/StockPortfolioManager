/**
 * SandboxedReport — a pre-built research page shown in a sandboxed frame.
 *
 * Used by the Research pages that are, for now, static snapshots of a
 * prototype's self-contained HTML (the Wind Map). The page lives
 * under `frontend/public/research/` and must be listed in SANDBOXED_PAGES in
 * `frontend/server/csp.mjs`, which sends it a matching `sandbox` CSP.
 *
 * The frame is sandboxed WITHOUT `allow-same-origin`: the framed page runs in
 * an opaque origin, so it can never read QuantUI's cookies, storage or BYOK
 * vault, or call /api as the user. Keep the attribute here, in one place.
 */
import type { ReactNode } from 'react';
import { Box, Typography } from '@mui/material';

/** The sandbox attribute every framed report gets. Never add allow-same-origin. */
export const REPORT_SANDBOX = 'allow-scripts';

type Props = {
  /** Page heading. */
  title: string;
  /** One or two sentences under the heading. */
  description: ReactNode;
  /** Where the server serves the pre-built page (a SANDBOXED_PAGES entry). */
  src: string;
  /** Accessible name of the frame. */
  frameTitle: string;
  /** Optional content between the description and the frame (e.g. a notice). */
  children?: ReactNode;
};

export default function SandboxedReport({ title, description, src, frameTitle, children }: Props) {
  return (
    <Box>
      <Typography variant="h4" sx={{ mb: 1 }}>
        {title}
      </Typography>
      <Typography variant="body2" sx={{ mb: 2, color: 'text.secondary' }}>
        {description}
      </Typography>
      {children}
      <Box
        component="iframe"
        src={src}
        title={frameTitle}
        sandbox={REPORT_SANDBOX}
        referrerPolicy="no-referrer"
        sx={{
          display: 'block',
          width: '100%',
          height: 'calc(100vh - 220px)',
          minHeight: 600,
          border: 0,
          borderRadius: 1,
        }}
      />
    </Box>
  );
}
