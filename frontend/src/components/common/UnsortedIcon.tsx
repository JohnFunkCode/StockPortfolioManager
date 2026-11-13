import RemoveIcon from '@mui/icons-material/Remove';
import type { SvgIconProps } from '@mui/material/SvgIcon';

/** The DataGrid header's icon for a column that isn't sorted: a dash.
 *
 * The grid's default is the ascending arrow at half opacity, which reads as
 * "sorted low to high" on the click that clears the sort. The grid still
 * greys this one out; only the glyph changes. Set once for every grid, as
 * `MuiDataGrid.defaultProps.slots` in `themes.ts`.
 *
 * `sortingOrder` is a prop the grid passes to this slot. It is dropped here
 * so it doesn't reach the `<svg>` as an unknown DOM attribute. */
export default function UnsortedIcon({
  sortingOrder: _sortingOrder,
  ...props
}: SvgIconProps & { sortingOrder?: unknown }) {
  return <RemoveIcon data-testid="unsorted-icon" {...props} />;
}
