import { useState } from 'react';
import { IconButton, Stack, TableCell, TableRow, TextField, Tooltip, Typography } from '@mui/material';
import EditIcon from '@mui/icons-material/Edit';
import DeleteIcon from '@mui/icons-material/Delete';
import SellIcon from '@mui/icons-material/Sell';
import CheckIcon from '@mui/icons-material/Check';
import CloseIcon from '@mui/icons-material/Close';
import ConfirmDialog from '../common/ConfirmDialog';
import CloseLotDialog from './CloseLotDialog';
import { useDeleteLot, useUpdateLot } from '../../hooks/usePortfolio';
import { formatCurrency, formatDollarsPerDay, formatPercentRaw, formatShares } from '../../utils/formatting';
import type { Lot } from '../../api/portfolioTypes';

function gainColor(value: number | null): string | undefined {
  if (value == null) return undefined;
  return value >= 0 ? '#10b981' : '#ef4444';
}

export default function LotRow({ lot }: { lot: Lot }) {
  const [editing, setEditing] = useState(false);
  const [purchasePrice, setPurchasePrice] = useState(String(lot.purchase_price ?? ''));
  const [quantity, setQuantity] = useState(String(lot.quantity ?? ''));
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [closing, setClosing] = useState(false);

  const updateLot = useUpdateLot();
  const deleteLot = useDeleteLot();

  const startEdit = () => {
    setPurchasePrice(String(lot.purchase_price ?? ''));
    setQuantity(String(lot.quantity ?? ''));
    setEditing(true);
  };

  const saveEdit = () => {
    updateLot.mutate(
      {
        lotId: lot.lot_id,
        data: {
          purchase_price: purchasePrice ? parseFloat(purchasePrice) : undefined,
          quantity: quantity ? parseFloat(quantity) : undefined,
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
        <TableCell />
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
      </TableRow>

      {updateLot.isError && editing && (
        <TableRow>
          <TableCell colSpan={8}>
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
    </>
  );
}
