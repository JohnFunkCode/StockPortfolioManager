import { useState } from 'react';
import { Button, Chip, Menu, MenuItem, Stack } from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import ArrowDropDownIcon from '@mui/icons-material/ArrowDropDown';
import { useBooks } from '../../hooks/useBooks';
import CreateBookDialog from './CreateBookDialog';
import type { Book } from '../../api/books';

/** Books shown as chips, the real book included. The rest go under "More". */
export const MAX_BOOK_CHIPS = 5;

interface Props {
  /** The selected paper book, or null for the real book. */
  selectedBookId: number | null;
  onSelect: (bookId: number | null) => void;
}

/**
 * Picks which book the Portfolio page shows (#431): the real book first, then
 * the paper books. The Real chip is always there, because an owner who has
 * never written anything has no book row yet, and it selects `null` — the
 * page then asks for no book, which the server reads as the real one.
 *
 * It never blocks the page: while the list loads, or if it fails, only the
 * Real chip shows.
 */
export default function BookSelector({ selectedBookId, onSelect }: Props) {
  const { data } = useBooks();
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null);
  const [creating, setCreating] = useState(false);

  const paper: Book[] = (data?.books ?? []).filter((book) => book.kind === 'paper');
  const fits = paper.length + 1 <= MAX_BOOK_CHIPS;
  const chips = fits ? paper : paper.slice(0, MAX_BOOK_CHIPS - 1);
  const overflow = fits ? [] : paper.slice(MAX_BOOK_CHIPS - 1);
  const selectedInOverflow = overflow.find((book) => book.book_id === selectedBookId);

  return (
    <Stack
      direction="row"
      spacing={1}
      alignItems="center"
      flexWrap="wrap"
      useFlexGap
      role="group"
      aria-label="Book"
      sx={{ mb: 2 }}
    >
      <Chip
        label="Real"
        clickable
        color={selectedBookId === null ? 'primary' : 'default'}
        aria-pressed={selectedBookId === null}
        onClick={() => onSelect(null)}
      />
      {chips.map((book) => (
        <Chip
          key={book.book_id}
          label={book.name}
          clickable
          variant={selectedBookId === book.book_id ? 'filled' : 'outlined'}
          color={selectedBookId === book.book_id ? 'primary' : 'default'}
          aria-pressed={selectedBookId === book.book_id}
          onClick={() => onSelect(book.book_id)}
        />
      ))}
      {overflow.length > 0 && (
        <>
          <Chip
            label={selectedInOverflow ? `More: ${selectedInOverflow.name}` : 'More'}
            clickable
            variant={selectedInOverflow ? 'filled' : 'outlined'}
            color={selectedInOverflow ? 'primary' : 'default'}
            aria-haspopup="menu"
            icon={<ArrowDropDownIcon />}
            onClick={(e) => setMenuAnchor(e.currentTarget)}
          />
          <Menu anchorEl={menuAnchor} open={menuAnchor !== null} onClose={() => setMenuAnchor(null)}>
            {overflow.map((book) => (
              <MenuItem
                key={book.book_id}
                selected={book.book_id === selectedBookId}
                onClick={() => {
                  setMenuAnchor(null);
                  onSelect(book.book_id);
                }}
              >
                {book.name}
              </MenuItem>
            ))}
          </Menu>
        </>
      )}
      <Button
        size="small"
        startIcon={<AddIcon />}
        onClick={() => setCreating(true)}
        sx={{ textTransform: 'none' }}
      >
        New paper book
      </Button>

      <CreateBookDialog
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={(book) => onSelect(book.book_id)}
      />
    </Stack>
  );
}
