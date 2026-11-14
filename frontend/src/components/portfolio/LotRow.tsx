import { useState } from 'react';
import { Box, IconButton, Stack, TableCell, TableRow, TextField, Tooltip, Typography } from '@mui/material';
import EditIcon from '@mui/icons-material/Edit';
import DeleteIcon from '@mui/icons-material/Delete';
import SellIcon from '@mui/icons-material/Sell';
import CheckIcon from '@mui/icons-material/Check';
import CloseIcon from '@mui/icons-material/Close';
import NotesIcon from '@mui/icons-material/Notes';
import StickyNote2OutlinedIcon from '@mui/icons-material/StickyNote2Outlined';
import ConfirmDialog from '../common/ConfirmDialog';
import CloseLotDialog from './CloseLotDialog';
import EditLotNoteDialog from './EditLotNoteDialog';
import { useDeleteLot, useUpdateLot } from '../../hooks/usePortfolio';
import { formatCurrency, formatDate, formatDollarsPerDay, formatPercentRaw, formatShares } from '../../utils/formatting';
import type { Lot } from '../../api/portfolioTypes';

/** The `YYYY-MM-DD` a date input needs; null/blank stays blank. */
function dateInputValue(value: string | null | undefined): string {
  return value ? value.slice(0, 10) : '';
}

function gainColor(value: number | null): string | undefined {
  if (value == null) return undefined;
  return value >= 0 ? '#10b981' : '#ef4444';
}

interface Props {
  lot: Lot;
  /** A paper book's lot (#431): shown, never edited, sold or deleted by hand,
   * because the system sets every paper fill. */
  readOnly?: boolean;
}

export default function LotRow({ lot, readOnly = false }: Props) {
  const [editing, setEditing] = useState(false);
  const [purchasePrice, setPurchasePrice] = useState(String(lot.purchase_price ?? ''));
  const [quantity, setQuantity] = useState(String(lot.quantity ?? ''));
  const [tradeDate, setTradeDate] = useState(dateInputValue(lot.trade_date));
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [closing, setClosing] = useState(false);
  const [editingNote, setEditingNote] = useState(false);

  const updateLot = useUpdateLot();
  const deleteLot = useDeleteLot();

  const startEdit = () => {
    setPurchasePrice(String(lot.purchase_price ?? ''));
    setQuantity(String(lot.quantity ?? ''));
    setTradeDate(dateInputValue(lot.trade_date));
    setEditing(true);
  };

  const saveEdit = () => {
    updateLot.mutate(
      {
        lotId: lot.lot_id,
        data: {
          purchase_price: purchasePrice ? parseFloat(purchasePrice) : undefined,
          quantity: quantity ? parseFloat(quantity) : undefined,
          // Only sent when changed, so saving a price edit never rewrites the date.
          trade_date:
            tradeDate && tradeDate !== dateInputValue(lot.trade_date) ? tradeDate : undefined,
        },
      },
      { onSuccess: () => setEditing(false) },
    );
  };

  const handleDelete = () => {
    deleteLot.mutate(lot.lot_id, { onSuccess: () => setConfirmDelete(false) });
  };

  return (
    <>
      <TableRow>
        <TableCell>
          {lot.notes && (
            <Tooltip
              title={<span style={{ whiteSpace: 'pre-wrap' }}>{lot.notes}</span>}
              slotProps={{ tooltip: { sx: { maxWidth: 420 } } }}
            >
              <Box component="span" role="img" aria-label="Lot note" sx={{ display: 'inline-flex' }}>
                <NotesIcon fontSize="small" color="action" />
              </Box>
            </Tooltip>
          )}
        </TableCell>
        <TableCell>
          {editing ? (
            <TextField
              size="small"
              type="date"
              value={tradeDate}
              onChange={(e) => setTradeDate(e.target.value)}
              inputProps={{ 'aria-label': 'Trade date' }}
              sx={{ width: 150 }}
            />
          ) : lot.trade_date ? (
            formatDate(lot.trade_date)
          ) : (
            '—'
          )}
        </TableCell>
        <TableCell>
          {editing ? (
            <TextField
              size="small"
              type="number"
              value={purchasePrice}
              onChange={(e) => setPurchasePrice(e.target.value)}
              inputProps={{ step: 0.01 }}
              sx={{ width: 100 }}
            />
          ) : (
            formatCurrency(lot.purchase_price)
          )}
        </TableCell>
        <TableCell>
          {editing ? (
            <TextField
              size="small"
              type="number"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              inputProps={{ step: 'any' }}
              sx={{ width: 90 }}
            />
          ) : (
            formatShares(lot.quantity)
          )}
        </TableCell>
        <TableCell sx={{ color: gainColor(lot.gain_loss_pct) }}>
          {formatPercentRaw(lot.gain_loss_pct)}
        </TableCell>
        <TableCell sx={{ color: gainColor(lot.gain_loss) }}>
          {formatCurrency(lot.gain_loss)}
        </TableCell>
        <TableCell>{lot.days_held ?? '—'}</TableCell>
        <TableCell>{formatDollarsPerDay(lot.dollars_per_day)}</TableCell>
        {/* The brokerage account inside the book — a field of the lot, never
            a second kind of book (#431). */}
        <TableCell>{lot.account || '—'}</TableCell>
        {!readOnly && (
          <TableCell align="right">
            {editing ? (
              <Stack direction="row" spacing={0.5} justifyContent="flex-end">
                <Tooltip title="Save">
                  <IconButton size="small" onClick={saveEdit} disabled={updateLot.isPending}>
                    <CheckIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
                <Tooltip title="Cancel">
                  <IconButton size="small" onClick={() => setEditing(false)} disabled={updateLot.isPending}>
                    <CloseIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              </Stack>
            ) : (
              <Stack direction="row" spacing={0.5} justifyContent="flex-end">
                <Tooltip title="Edit lot">
                  <IconButton size="small" onClick={startEdit}>
                    <EditIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
                <Tooltip title="Edit note">
                  <IconButton size="small" onClick={() => setEditingNote(true)}>
                    <StickyNote2OutlinedIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
                <Tooltip title="Sell / close lot">
                  <IconButton size="small" onClick={() => setClosing(true)}>
                    <SellIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
                <Tooltip title="Delete lot">
                  <IconButton size="small" onClick={() => setConfirmDelete(true)}>
                    <DeleteIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              </Stack>
            )}
          </TableCell>
        )}
      </TableRow>

      {updateLot.isError && editing && (
        <TableRow>
          <TableCell colSpan={10}>
            <Typography variant="caption" sx={{ color: '#ef4444' }}>
              {(updateLot.error as Error).message}
            </Typography>
          </TableCell>
        </TableRow>
      )}

      <ConfirmDialog
        open={confirmDelete}
        title="Delete lot"
        message={`Delete this ${lot.symbol} lot (${formatShares(lot.quantity)} shares @ ${formatCurrency(lot.purchase_price)})? This cannot be undone.`}
        confirmLabel="Delete"
        loading={deleteLot.isPending}
        onConfirm={handleDelete}
        onCancel={() => setConfirmDelete(false)}
      />

      {closing && <CloseLotDialog open lot={lot} onClose={() => setClosing(false)} />}
      {editingNote && <EditLotNoteDialog open lot={lot} onClose={() => setEditingNote(false)} />}
    </>
  );
}
