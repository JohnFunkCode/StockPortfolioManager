import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import PaperBookBanner from './PaperBookBanner';
import { mockApi, renderWithProviders } from '../../testUtils';
import type { Book } from '../../api/books';

afterEach(() => vi.unstubAllGlobals());

const BOOK: Book = {
  book_id: 7, name: 'Momentum', kind: 'paper', starting_cash: 2500.5,
  created_at: '2026-11-02T00:00:00Z', archived_at: null,
};

function mount(book: Book | undefined, archive: object = { ...BOOK, archived_at: '2026-11-14T00:00:00Z' }) {
  const onArchived = vi.fn();
  const api = mockApi([
    [/\/api\/books\/7\/archive$/, archive],
    ['/api/books', { books: [] }],
  ]);
  renderWithProviders(<PaperBookBanner book={book} onArchived={onArchived} />);
  return { api, onArchived };
}

describe('PaperBookBanner', () => {
  it('names the book, says it is simulated, and shows its starting cash', () => {
    mount(BOOK);
    const banner = screen.getByRole('status');
    expect(within(banner).getByText('Paper book: Momentum')).toBeInTheDocument();
    expect(within(banner).getByText(/Simulated positions\. Nothing here is a real holding\./)).toBeInTheDocument();
    expect(within(banner).getByText(/Starting cash \$2,500\.50\./)).toBeInTheDocument();
  });

  it('still warns when the book is not in the loaded list, and offers no archive', () => {
    mount(undefined);
    expect(screen.getByText('Paper book')).toBeInTheDocument();
    expect(screen.getByText(/Nothing here is a real holding/)).toBeInTheDocument();
    expect(screen.queryByText(/Starting cash/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Archive book' })).not.toBeInTheDocument();
  });

  it('archives only after confirmation', async () => {
    const { api, onArchived } = mount(BOOK);
    fireEvent.click(screen.getByRole('button', { name: 'Archive book' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/Its positions are kept, not deleted/)).toBeInTheDocument();
    expect(api.calls.some(([url]) => url.includes('/archive'))).toBe(false);

    fireEvent.click(within(dialog).getByRole('button', { name: 'Archive' }));
    await waitFor(() => expect(onArchived).toHaveBeenCalledTimes(1));
    const call = api.calls.find(([url]) => url.endsWith('/api/books/7/archive'));
    expect(call?.[1]?.method).toBe('POST');
  });

  it('cancelling the confirmation archives nothing', async () => {
    const { api, onArchived } = mount(BOOK);
    fireEvent.click(screen.getByRole('button', { name: 'Archive book' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(api.calls.some(([url]) => url.includes('/archive'))).toBe(false);
    expect(onArchived).not.toHaveBeenCalled();
  });

  it('shows why an archive failed and stays on the book', async () => {
    const { onArchived } = mount(BOOK, { __status: 404, error: 'book 7 not found' });
    fireEvent.click(screen.getByRole('button', { name: 'Archive book' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Archive' }));
    expect(await screen.findByText('book 7 not found')).toBeInTheDocument();
    expect(onArchived).not.toHaveBeenCalled();
  });
});
