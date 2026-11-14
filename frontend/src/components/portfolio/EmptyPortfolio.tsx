import { Box, Typography } from '@mui/material';

/** Friendly empty-state for a provisioned owner with zero positions — rendered
 * inside the normal Layout, distinct from RestrictedAccess (a 403). Must never
 * fire on a network error or 500; only on a successful, empty response.
 * `paper` is the same state for a paper book (#431), which has no Add Lot. */
export default function EmptyPortfolio({ paper = false }: { paper?: boolean }) {
  return (
    <Box sx={{ textAlign: 'center', mt: 8 }}>
      <Typography variant="h5" gutterBottom>
        {paper ? 'This paper book is empty' : 'Your portfolio is empty'}
      </Typography>
      <Typography color="text.secondary" sx={{ maxWidth: 480, mx: 'auto' }}>
        {paper
          ? 'It holds no simulated positions yet.'
          : 'Add your first position to start tracking gains, losses, and Harvester plans.'}
      </Typography>
    </Box>
  );
}
