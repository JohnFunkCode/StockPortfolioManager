/**
 * SeedEmptyBook — "copy my real holdings" for an empty paper book (#431 step
 * 3.5). Shown only while the book has had no orders: that is the server's own
 * rule for a seed, and the same orders query the Orders list makes.
 */
import { Alert, Box, Button, Typography } from '@mui/material';
import { useOrders, useSeedBook } from '../../hooks/useOrders';
import { seedSummary } from './orderWords';

export default function SeedEmptyBook({ bookId }: { bookId: number }) {
  const { data } = useOrders(bookId);
  const seed = useSeedBook();

  if (seed.data) {
    return (
      <Alert severity="info" sx={{ mt: 2, maxWidth: 480, mx: 'auto', textAlign: 'left' }}>
        {seedSummary(seed.data).map((line) => (
          <Box key={line}>{line}</Box>
        ))}
      </Alert>
    );
  }
  // Unknown until the orders load: a book with orders can't be seeded.
  if (!data || data.orders.length > 0) return null;

  return (
    <Box sx={{ mt: 2 }}>
      <Button variant="outlined" disabled={seed.isPending} onClick={() => seed.mutate(bookId)}>
        {seed.isPending ? 'Copying holdings…' : 'Copy my real holdings'}
      </Button>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
        Each holding is copied at its current price, at no cost to this book's cash.
      </Typography>
      {seed.isError && (
        <Typography variant="caption" role="alert" sx={{ display: 'block', color: 'var(--qc-ink-red)' }}>
          {(seed.error as Error).message}
        </Typography>
      )}
    </Box>
  );
}
