import { Box, CircularProgress } from '@mui/material';

export default function LoadingSpinner() {
  return (
    <Box display="flex" justifyContent="center" alignItems="center" minHeight={200}>
      <CircularProgress />
    </Box>
  );
}
