import { useEffect } from 'react';
import { Box, Typography } from '@mui/material';

export const RESTRICTED_ACCESS_REDIRECT_URL = 'https://www.fbi.gov/investigate/cyber';
export const RESTRICTED_ACCESS_REDIRECT_DELAY_MS = 10_000;

/** Full-page terminal state for an unmapped principal (issue #126 decision
 * #2/#4). Deliberately standalone — no Layout, no nav, no identifying
 * information — and rendered outside the Layout-wrapped route tree in App.tsx. */
export default function RestrictedAccess() {
  useEffect(() => {
    const timer = setTimeout(() => {
      window.location.href = RESTRICTED_ACCESS_REDIRECT_URL;
    }, RESTRICTED_ACCESS_REDIRECT_DELAY_MS);
    return () => clearTimeout(timer);
  }, []);

  return (
    <Box
      sx={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100vh',
        width: '100vw',
        bgcolor: 'background.default',
        p: 4,
        textAlign: 'center',
      }}
    >
      <Typography variant="h6" component="p" sx={{ maxWidth: 600 }}>
        This system is restricted to authorized users. Individuals who attempt
        unauthorized access will be prosecuted. If you are unauthorized,
        terminate access now.
      </Typography>
    </Box>
  );
}
