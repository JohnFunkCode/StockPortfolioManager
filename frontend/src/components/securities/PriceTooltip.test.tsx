import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { ThemeProvider } from '@mui/material/styles';

import PriceTooltip from './PriceTooltip';
import { darkTheme, lightTheme } from '../../themes';

afterEach(cleanup);

describe('PriceTooltip', () => {
  it.each([
    ['light', lightTheme],
    ['dark', darkTheme],
  ])('uses the %s theme colors', (_name, theme) => {
    const { container } = render(
      <ThemeProvider theme={theme}>
        <PriceTooltip />
      </ThemeProvider>,
    );
    const tooltip = container.querySelector('#price-tooltip') as HTMLDivElement;

    expect(tooltip).toHaveStyle({
      backgroundColor: theme.palette.background.paper,
      color: theme.palette.text.primary,
      border: `1px solid ${theme.palette.divider}`,
      boxShadow: theme.shadows[4],
    });
  });
});
