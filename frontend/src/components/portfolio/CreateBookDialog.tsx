import { useState } from 'react';
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import { useCreateBook } from '../../hooks/useBooks';
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
  const createBook = useCreateBook();

  const close = () => {
    setName('');
    setCash('');
    setCashError('');
    createBook.reset();
    onClose();
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
          close();
          onCreated(book);
        },
      },
    );
  };

  return (
    <Dialog open={open} onClose={close} maxWidth="xs" fullWidth>
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
          {createBook.isError && (
            <Typography variant="caption" role="alert" sx={{ color: 'var(--qc-ink-red)' }}>
              {(createBook.error as Error).message}
            </Typography>
          )}
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={close} disabled={createBook.isPending}>
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={submit}
          disabled={createBook.isPending || !name.trim()}
        >
          {createBook.isPending ? 'Saving…' : 'Create book'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
