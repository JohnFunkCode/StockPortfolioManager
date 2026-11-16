import { useState } from 'react';
import {
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import { useCreateBook } from '../../hooks/useBooks';
import { useSeedBook } from '../../hooks/useOrders';
import type { Book } from '../../api/books';

/** Matches MAX_BOOK_NAME_LENGTH in quantcore/services/books.py. The server
 * is the check; this only stops the field taking more. */
const MAX_NAME_LENGTH = 60;

interface Props {
  open: boolean;
  onClose: () => void;
  /** Called with the new book once it is saved. */
  onCreated: (book: Book) => void;
}

export default function CreateBookDialog({ open, onClose, onCreated }: Props) {
  const [name, setName] = useState('');
  const [cash, setCash] = useState('');
  const [cashError, setCashError] = useState('');
  // Start the book with a copy of the real book's holdings (decision 11).
  const [seed, setSeed] = useState(false);
  // Set once the book is saved but its holdings could not be copied.
  const [unseeded, setUnseeded] = useState<Book | null>(null);
  const createBook = useCreateBook();
  const seedBook = useSeedBook();
  const busy = createBook.isPending || seedBook.isPending;

  const close = () => {
    setName('');
    setCash('');
    setCashError('');
    setSeed(false);
    setUnseeded(null);
    createBook.reset();
    seedBook.reset();
    onClose();
  };

  const finish = (book: Book) => {
    close();
    onCreated(book);
  };

  const submit = () => {
    // A blank field is zero. Anything else must be a number: NaN would go out
    // as JSON null and come back as a validation error nobody can read.
    const startingCash = cash.trim() ? Number(cash) : 0;
    if (!Number.isFinite(startingCash) || startingCash < 0) {
      setCashError('Starting cash must be a number, zero or more.');
      return;
    }
    createBook.mutate(
      { name: name.trim(), starting_cash: startingCash },
      {
        onSuccess: (book) => {
          if (!seed) {
            finish(book);
            return;
          }
          // The book exists either way. A failed copy keeps the dialog open
          // to say so, and the empty book offers the copy again.
          seedBook.mutate(book.book_id, {
            onSuccess: () => finish(book),
            onError: () => setUnseeded(book),
          });
        },
      },
    );
  };

  return (
    <Dialog open={open} onClose={busy ? undefined : () => (unseeded ? finish(unseeded) : close())} maxWidth="xs" fullWidth>
      <DialogTitle>New paper book</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <Typography variant="body2" color="text.secondary">
            A paper book holds simulated positions. Nothing in it is a real holding.
          </Typography>
          <TextField
            label="Name *"
            size="small"
            value={name}
            onChange={(e) => setName(e.target.value)}
            inputProps={{ maxLength: MAX_NAME_LENGTH }}
            autoFocus
          />
          <TextField
            label="Starting cash"
            size="small"
            type="number"
            value={cash}
            onChange={(e) => {
              setCash(e.target.value);
              setCashError('');
            }}
            inputProps={{ min: 0, step: 0.01 }}
            placeholder="0"
            error={Boolean(cashError)}
            helperText={cashError || undefined}
          />
          <FormControlLabel
            control={
              <Checkbox
                size="small"
                checked={seed}
                onChange={(e) => setSeed(e.target.checked)}
                disabled={busy || unseeded !== null}
              />
            }
            label="Start with a copy of my real holdings"
          />
          {seed && (
            <Typography variant="caption" color="text.secondary">
              Each holding is copied at its current price, at no cost, on top of the starting
              cash. When the market is closed the copies arrive at the next session's opening
              price.
            </Typography>
          )}
          {unseeded && (
            <Typography variant="caption" role="alert" sx={{ color: 'var(--qc-ink-red)' }}>
              The book was created, but its holdings were not copied:{' '}
              {(seedBook.error as Error | null)?.message ?? 'the copy failed'}. Open the book to
              try again.
            </Typography>
          )}
          {createBook.isError && (
            <Typography variant="caption" role="alert" sx={{ color: 'var(--qc-ink-red)' }}>
              {(createBook.error as Error).message}
            </Typography>
          )}
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        {unseeded ? (
          <Button variant="contained" onClick={() => finish(unseeded)}>
            Open the book
          </Button>
        ) : (
          <>
            <Button onClick={close} disabled={busy}>
              Cancel
            </Button>
            <Button variant="contained" onClick={submit} disabled={busy || !name.trim()}>
              {createBook.isPending ? 'Saving…' : seedBook.isPending ? 'Copying holdings…' : 'Create book'}
            </Button>
          </>
        )}
      </DialogActions>
    </Dialog>
  );
}
