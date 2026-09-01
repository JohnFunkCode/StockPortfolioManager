import { useTheme } from '@mui/material/styles';

/** Shared fixed-position host for D3 security-chart tooltips. */
export default function PriceTooltip() {
  const theme = useTheme();

  return (
    <div
      id="price-tooltip"
      style={{
        display: 'none',
        position: 'fixed',
        background: theme.palette.background.paper,
        color: theme.palette.text.primary,
        border: `1px solid ${theme.palette.divider}`,
        padding: '6px 10px',
        borderRadius: 6,
        fontSize: 12,
        pointerEvents: 'none',
        zIndex: 9999,
        boxShadow: theme.shadows[4],
        lineHeight: 1.6,
      }}
    />
  );
}
