/**
 * The DataGrid's unsorted-column icon is a dash on every theme. The default is
 * a greyed-out ascending arrow, which looks like a sort direction.
 */
import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ThemeProvider } from '@mui/material/styles';
import { DataGrid } from '@mui/x-data-grid';

import UnsortedIcon from './UnsortedIcon';
import { themes } from '../../themes';

function renderGrid(themeName: keyof typeof themes) {
  return render(
    <ThemeProvider theme={themes[themeName]}>
      <div style={{ width: 400, height: 300 }}>
        <DataGrid
          rows={[{ id: 1, n: 2 }, { id: 2, n: 1 }]}
          columns={[{ field: 'n', headerName: 'N', width: 150 }]}
          disableVirtualization
        />
      </div>
    </ThemeProvider>,
  );
}

describe('UnsortedIcon', () => {
  it('renders a dash and keeps the sortingOrder prop off the svg', () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<UnsortedIcon sortingOrder={['asc', 'desc', null]} fontSize="small" />);
    const icon = screen.getByTestId('unsorted-icon');
    expect(icon.tagName.toLowerCase()).toBe('svg');
    expect(icon).not.toHaveAttribute('sortingorder');
    expect(error).not.toHaveBeenCalled();
    error.mockRestore();
  });

  it.each(Object.keys(themes) as (keyof typeof themes)[])(
    'is the unsorted icon of a DataGrid on the %s theme, and gives way to the arrows',
    async (themeName) => {
      renderGrid(themeName);
      const header = screen.getByRole('columnheader', { name: 'N' });
      expect(within(header).getByTestId('unsorted-icon')).toBeInTheDocument();

      await userEvent.click(within(header).getByText('N'));
      expect(header).toHaveAttribute('aria-sort', 'ascending');
      expect(within(header).queryByTestId('unsorted-icon')).toBeNull();

      await userEvent.click(within(header).getByText('N'));
      await userEvent.click(within(header).getByText('N'));
      expect(header).toHaveAttribute('aria-sort', 'none');
      expect(within(header).getByTestId('unsorted-icon')).toBeInTheDocument();
    },
  );
});
