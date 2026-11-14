import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import BookSelector, { MAX_BOOK_CHIPS } from './BookSelector';
import { mockApi, renderWithProviders } from '../../testUtils';

afterEach(() => vi.unstubAllGlobals());

const book = (book_id: number, name: string, kind = 'paper') => ({
  book_id, name, kind, starting_cash: 0, created_at: '2026-11-01T00:00:00Z', archived_at: null,
});
const REAL = book(1, 'Real', 'real');
const papers = (count: number) => Array.from({ length: count }, (_, i) => book(10 + i, `Paper ${i + 1}`));

function mount(books: object[] | { __status: number }, selected: number | null = null, extra: Array<[string | RegExp, object]> = []) {
  const onSelect = vi.fn();
  const api = mockApi([
    ...extra,
    ['/api/books', Array.isArray(books) ? { books } : books],
  ]);
  renderWithProviders(<BookSelector selectedBookId={selected} onSelect={onSelect} />);
  return { api, onSelect };
}

describe('BookSelector', () => {
  it('shows only the Real chip while the books load', () => {
    mockApi([['/api/books', () => new Promise(() => {})]]);
    renderWithProviders(<BookSelector selectedBookId={null} onSelect={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Real' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('button', { name: 'More' })).not.toBeInTheDocument();
  });

  it('shows only the Real chip when the list fails', async () => {
    const { api } = mount({ __status: 500 });
    await waitFor(() => expect(api.calls.length).toBeGreaterThan(0));
    expect(screen.getByRole('button', { name: 'Real' })).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows a Real chip for an owner with no book yet', async () => {
    const { api, onSelect } = mount([]);
    await waitFor(() => expect(api.calls.length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('button', { name: 'Real' }));
    expect(onSelect).toHaveBeenCalledWith(null);
  });

  it('lists the real book once, then each paper book as a chip', async () => {
    const { onSelect } = mount([REAL, ...papers(2)], 11);
    const second = await screen.findByRole('button', { name: 'Paper 2' });
    expect(screen.getAllByRole('button', { name: 'Real' })).toHaveLength(1);
    expect(second).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Real' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.queryByRole('button', { name: /More/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Paper 1' }));
    expect(onSelect).toHaveBeenCalledWith(10);
  });

  it('shows five books as chips with no More', async () => {
    mount([REAL, ...papers(MAX_BOOK_CHIPS - 1)]);
    expect(await screen.findByRole('button', { name: `Paper ${MAX_BOOK_CHIPS - 1}` })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /More/ })).not.toBeInTheDocument();
  });

  it('puts the books past five under More', async () => {
    const { onSelect } = mount([REAL, ...papers(6)]);
    const more = await screen.findByRole('button', { name: 'More' });
    expect(screen.getByRole('button', { name: 'Paper 4' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Paper 5' })).not.toBeInTheDocument();

    fireEvent.click(more);
    const menu = await screen.findByRole('menu');
    expect(within(menu).getAllByRole('menuitem').map((item) => item.textContent)).toEqual(['Paper 5', 'Paper 6']);
    fireEvent.click(within(menu).getByText('Paper 6'));
    expect(onSelect).toHaveBeenCalledWith(15);
  });

  it('names the selected book on the More chip', async () => {
    mount([REAL, ...papers(6)], 15);
    expect(await screen.findByRole('button', { name: 'More: Paper 6' })).toBeInTheDocument();
  });

  it('creates a paper book and selects it', async () => {
    const { api, onSelect } = mount([REAL], null, [
      [/\/api\/books$/, (_url: string, init?: RequestInit) =>
        init?.method === 'POST' ? book(42, 'Momentum') : { books: [REAL] }],
    ] as never);
    fireEvent.click(screen.getByRole('button', { name: /New paper book/i }));
    const dialog = await screen.findByRole('dialog');
    const create = within(dialog).getByRole('button', { name: 'Create book' });
    expect(create).toBeDisabled();

    fireEvent.change(within(dialog).getByLabelText(/Name/), { target: { value: '  Momentum ' } });
    fireEvent.change(within(dialog).getByLabelText(/Starting cash/), { target: { value: '2500.5' } });
    fireEvent.click(create);

    await waitFor(() => expect(onSelect).toHaveBeenCalledWith(42));
    const post = api.calls.find(([, init]) => init?.method === 'POST');
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ name: 'Momentum', starting_cash: 2500.5 });
  });

  it('shows why a book could not be created and keeps the dialog open', async () => {
    const { onSelect } = mount([REAL], null, [
      [/\/api\/books$/, (_url: string, init?: RequestInit) =>
        init?.method === 'POST'
          ? { __status: 422, error: "a book named 'Momentum' already exists" }
          : { books: [REAL] }],
    ] as never);
    fireEvent.click(screen.getByRole('button', { name: /New paper book/i }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/Name/), { target: { value: 'Momentum' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create book' }));

    expect(await within(dialog).findByRole('alert')).toHaveTextContent('already exists');
    expect(onSelect).not.toHaveBeenCalled();

    // Cancel clears the error and the fields for the next open.
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /New paper book/i }));
    const reopened = await screen.findByRole('dialog');
    expect(within(reopened).getByLabelText(/Name/)).toHaveValue('');
    expect(within(reopened).queryByRole('alert')).not.toBeInTheDocument();
  });

  it('sends zero starting cash when the field is left blank', async () => {
    const { api, onSelect } = mount([REAL], null, [
      [/\/api\/books$/, (_url: string, init?: RequestInit) =>
        init?.method === 'POST' ? book(43, 'Blank') : { books: [REAL] }],
    ] as never);
    fireEvent.click(screen.getByRole('button', { name: /New paper book/i }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/Name/), { target: { value: 'Blank' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create book' }));
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith(43));
    const post = api.calls.find(([, init]) => init?.method === 'POST');
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ name: 'Blank', starting_cash: 0 });
  });
});
