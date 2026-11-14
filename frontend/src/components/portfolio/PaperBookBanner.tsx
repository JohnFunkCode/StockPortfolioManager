import { useState } from 'react';
import { Box, Button, Stack, Typography } from '@mui/material';
import ConfirmDialog from '../common/ConfirmDialog';
import { useArchiveBook } from '../../hooks/useBooks';
import { formatCurrency } from '../../utils/formatting';
import type { Book } from '../../api/books';

interface Props {
  /** The selected paper book; undefined when it isn't in the loaded list. */
  book: Book | undefined;
  /** Called once the book is archived, so the page can leave it. */
  onArchived: () => void;
}

/**
 * Says, on every view of a paper book, that it is simulated (#431). A paper
 * position must never be mistaken for a real holding.
 */
export default function PaperBookBanner({ book, onArchived }: Props) {
  const [confirming, setConfirming] = useState(false);
  const archiveBook = useArchiveBook();

  const archive = () => {
    if (!book) return;
    archiveBook.mutate(book.book_id, {
      onSuccess: () => {
        setConfirming(false);
        onArchived();
      },
    });
  };

  return (
    <Box
      role="status"
      sx={{
        mb: 2,
        px: 2,
        py: 1,
        borderRadius: 1,
        bgcolor: 'var(--qc-tint-amber)',
        border: '1px solid var(--qc-tint-amber-deep)',
      }}
    >
      <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={2}>
        <Box>
          <Typography variant="body2" sx={{ fontWeight: 600, color: 'var(--qc-ink-amber)' }}>
            Paper book{book ? `: ${book.name}` : ''}
          </Typography>
          <Typography variant="body2" sx={{ color: 'var(--qc-text-soft)' }}>
            Simulated positions. Nothing here is a real holding.
            {book ? ` Starting cash ${formatCurrency(book.starting_cash)}.` : ''}
          </Typography>
          {archiveBook.isError && (
            <Typography variant="caption" role="alert" sx={{ color: 'var(--qc-ink-red)' }}>
              {(archiveBook.error as Error).message}
            </Typography>
          )}
        </Box>
        {book && (
          <Button
            size="small"
            color="inherit"
            sx={{ textTransform: 'none', flexShrink: 0 }}
            onClick={() => setConfirming(true)}
          >
            Archive book
          </Button>
        )}
      </Stack>

      <ConfirmDialog
        open={confirming}
        title="Archive paper book"
        message={`Archive "${book?.name ?? ''}"? It leaves the book list. Its positions are kept, not deleted.`}
        confirmLabel="Archive"
        loading={archiveBook.isPending}
        onConfirm={archive}
        onCancel={() => setConfirming(false)}
      />
    </Box>
  );
}
