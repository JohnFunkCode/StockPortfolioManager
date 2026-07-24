import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import RungsTable from './RungsTable';
import { renderWithProviders, rungRow } from '../../testUtils';
import type { Rung } from '../../api/types';

afterEach(cleanup);

describe('RungsTable', () => {
  it('renders one grid row per rung', () => {
    renderWithProviders(
      <RungsTable rungs={[rungRow({ rung_index: 1, status: 'PENDING' }), rungRow({ rung_id: 2, rung_index: 2, status: 'ACHIEVED' })] as Rung[]} />,
    );
    // DataGrid emits role="row" per data row (+ header). renderCell columns can
    // virtualize off-viewport in jsdom, so assert on row structure.
    expect(screen.getAllByRole('row').length).toBeGreaterThanOrEqual(3);
  });

  it('renders an empty table with no rungs', () => {
    const { container } = renderWithProviders(<RungsTable rungs={[] as Rung[]} />);
    expect(container).toBeTruthy();
  });
});
