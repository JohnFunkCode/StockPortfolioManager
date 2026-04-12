import { createTheme, alpha } from '@mui/material/styles';
import type {} from '@mui/x-data-grid/themeAugmentation';

// ---------------------------------------------------------------------------
// Synthwave palette
// ---------------------------------------------------------------------------
const SW = {
  bg:            '#0a0015',   // near-black deep purple — body background
  paper:         '#130926',   // dark purple — card / surface
  paperElevated: '#1c0f35',   // slightly lighter for nested surfaces
  primary:       '#ff2d78',   // hot neon pink
  primaryDark:   '#c4005a',
  secondary:     '#00e5ff',   // electric cyan
  secondaryDark: '#009ab5',
  success:       '#00e676',   // neon green
  warning:       '#ff9100',   // neon amber
  error:         '#ff3366',   // neon red-pink
  info:          '#e040fb',   // neon magenta
  textPrimary:   '#f0e6ff',   // soft lavender-white
  textSecondary: '#b39ddb',   // muted lavender
  divider:       '#2a0f4a',
};

export const glowPink  = `0 0 8px ${alpha(SW.primary,   0.6)}, 0 0 20px ${alpha(SW.primary,   0.25)}`;
export const glowCyan  = `0 0 8px ${alpha(SW.secondary, 0.6)}, 0 0 20px ${alpha(SW.secondary, 0.25)}`;
export const glowGreen = `0 0 8px ${alpha(SW.success,   0.55)}, 0 0 16px ${alpha(SW.success,  0.2)}`;

const theme = createTheme({
  palette: {
    mode: 'dark',
    primary:    { main: SW.primary,    dark: SW.primaryDark,   contrastText: '#fff' },
    secondary:  { main: SW.secondary,  dark: SW.secondaryDark, contrastText: '#000' },
    success:    { main: SW.success },
    warning:    { main: SW.warning },
    error:      { main: SW.error   },
    info:       { main: SW.info    },
    background: { default: SW.bg, paper: SW.paper },
    text:       { primary: SW.textPrimary, secondary: SW.textSecondary },
    divider:    SW.divider,
  },

  typography: {
    fontFamily: '"Inter", "Segoe UI", system-ui, sans-serif',
    h1: { fontFamily: '"Orbitron", "Inter", sans-serif', fontWeight: 700 },
    h2: { fontFamily: '"Orbitron", "Inter", sans-serif', fontWeight: 700 },
    h3: { fontFamily: '"Orbitron", "Inter", sans-serif', fontWeight: 700 },
    h4: { fontFamily: '"Orbitron", "Inter", sans-serif', fontWeight: 700 },
    h5: { fontFamily: '"Orbitron", "Inter", sans-serif', fontWeight: 600 },
    h6: { fontFamily: '"Orbitron", "Inter", sans-serif', fontWeight: 600 },
    subtitle1: { fontWeight: 600 },
    subtitle2: { fontWeight: 600 },
  },

  shape: { borderRadius: 8 },

  components: {
    // -----------------------------------------------------------------------
    // AppBar
    // -----------------------------------------------------------------------
    MuiAppBar: {
      styleOverrides: {
        root: {
          background: 'linear-gradient(135deg, #0d0020 0%, #0a0015 100%)',
          boxShadow: `0 1px 0 0 ${SW.divider}, 0 2px 16px 0 ${alpha(SW.primary, 0.15)}`,
          borderBottom: `1px solid ${alpha(SW.primary, 0.3)}`,
        },
      },
    },

    // -----------------------------------------------------------------------
    // Paper
    // -----------------------------------------------------------------------
    MuiPaper: {
      styleOverrides: {
        root: {
          backgroundImage: 'none',
          backgroundColor: SW.paper,
          border: `1px solid ${alpha(SW.primary, 0.12)}`,
        },
        elevation2: { backgroundColor: SW.paperElevated },
        elevation3: { backgroundColor: SW.paperElevated },
      },
    },

    // -----------------------------------------------------------------------
    // Card
    // -----------------------------------------------------------------------
    MuiCard: {
      styleOverrides: {
        root: {
          backgroundColor: SW.paper,
          border: `1px solid ${alpha(SW.primary, 0.18)}`,
          transition: 'border-color 0.2s, box-shadow 0.2s',
          '&:hover': {
            borderColor: alpha(SW.primary, 0.45),
            boxShadow: glowPink,
          },
        },
      },
    },

    // -----------------------------------------------------------------------
    // Button
    // -----------------------------------------------------------------------
    MuiButton: {
      styleOverrides: {
        root: {
          textTransform: 'none',
          fontWeight: 600,
          letterSpacing: '0.03em',
          transition: 'box-shadow 0.2s, transform 0.1s',
        },
        containedPrimary: {
          background: `linear-gradient(135deg, ${SW.primary} 0%, #c000b0 100%)`,
          boxShadow: `0 2px 12px ${alpha(SW.primary, 0.4)}`,
          '&:hover': {
            background: `linear-gradient(135deg, #ff5590 0%, ${SW.primary} 100%)`,
            boxShadow: glowPink,
            transform: 'translateY(-1px)',
          },
        },
        containedSecondary: {
          background: `linear-gradient(135deg, ${SW.secondary} 0%, #0090c0 100%)`,
          color: '#000',
          boxShadow: `0 2px 12px ${alpha(SW.secondary, 0.35)}`,
          '&:hover': {
            background: `linear-gradient(135deg, #66f4ff 0%, ${SW.secondary} 100%)`,
            boxShadow: glowCyan,
            transform: 'translateY(-1px)',
          },
        },
        outlinedPrimary: {
          borderColor: alpha(SW.primary, 0.6),
          color: SW.primary,
          '&:hover': {
            borderColor: SW.primary,
            backgroundColor: alpha(SW.primary, 0.08),
            boxShadow: `0 0 10px ${alpha(SW.primary, 0.3)}`,
          },
        },
        outlinedSecondary: {
          borderColor: alpha(SW.secondary, 0.6),
          color: SW.secondary,
          '&:hover': {
            borderColor: SW.secondary,
            backgroundColor: alpha(SW.secondary, 0.08),
            boxShadow: `0 0 10px ${alpha(SW.secondary, 0.3)}`,
          },
        },
      },
    },

    // -----------------------------------------------------------------------
    // Chip
    // -----------------------------------------------------------------------
    MuiChip: {
      styleOverrides: {
        root: { fontWeight: 600 },
        colorPrimary: {
          backgroundColor: alpha(SW.primary, 0.15),
          color: SW.primary,
          border: `1px solid ${alpha(SW.primary, 0.4)}`,
        },
        colorSecondary: {
          backgroundColor: alpha(SW.secondary, 0.12),
          color: SW.secondary,
          border: `1px solid ${alpha(SW.secondary, 0.4)}`,
        },
        colorSuccess: {
          backgroundColor: alpha(SW.success, 0.12),
          color: SW.success,
          border: `1px solid ${alpha(SW.success, 0.4)}`,
        },
        colorError: {
          backgroundColor: alpha(SW.error, 0.12),
          color: SW.error,
          border: `1px solid ${alpha(SW.error, 0.4)}`,
        },
        colorInfo: {
          backgroundColor: alpha(SW.info, 0.12),
          color: SW.info,
          border: `1px solid ${alpha(SW.info, 0.4)}`,
        },
      },
    },

    // -----------------------------------------------------------------------
    // Tabs
    // -----------------------------------------------------------------------
    MuiTabs: {
      styleOverrides: {
        indicator: {
          backgroundColor: SW.primary,
          boxShadow: `0 0 8px ${alpha(SW.primary, 0.8)}`,
          height: 2,
        },
      },
    },
    MuiTab: {
      styleOverrides: {
        root: {
          textTransform: 'none',
          fontWeight: 600,
          color: SW.textSecondary,
          '&.Mui-selected': { color: SW.primary },
          '&:hover': { color: alpha(SW.primary, 0.8) },
        },
      },
    },

    // -----------------------------------------------------------------------
    // TextField / Input
    // -----------------------------------------------------------------------
    MuiOutlinedInput: {
      styleOverrides: {
        root: {
          '& .MuiOutlinedInput-notchedOutline': {
            borderColor: alpha(SW.primary, 0.25),
          },
          '&:hover .MuiOutlinedInput-notchedOutline': {
            borderColor: alpha(SW.primary, 0.55),
          },
          '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
            borderColor: SW.primary,
            boxShadow: `0 0 0 2px ${alpha(SW.primary, 0.15)}`,
          },
        },
      },
    },
    MuiInputLabel: {
      styleOverrides: {
        root: {
          color: SW.textSecondary,
          '&.Mui-focused': { color: SW.primary },
        },
      },
    },
    MuiSelect: {
      styleOverrides: {
        icon: { color: SW.textSecondary },
      },
    },

    // -----------------------------------------------------------------------
    // Table
    // -----------------------------------------------------------------------
    MuiTableHead: {
      styleOverrides: {
        root: {
          '& .MuiTableCell-head': {
            backgroundColor: alpha(SW.primary, 0.08),
            color: SW.primary,
            fontWeight: 700,
            borderBottom: `1px solid ${alpha(SW.primary, 0.3)}`,
            textTransform: 'uppercase',
            fontSize: '0.7rem',
            letterSpacing: '0.08em',
          },
        },
      },
    },
    MuiTableRow: {
      styleOverrides: {
        root: {
          '&:hover': { backgroundColor: alpha(SW.primary, 0.05) },
          '& .MuiTableCell-root': { borderBottom: `1px solid ${SW.divider}` },
        },
      },
    },

    // -----------------------------------------------------------------------
    // DataGrid
    // -----------------------------------------------------------------------
    MuiDataGrid: {
      styleOverrides: {
        root: {
          border: `1px solid ${alpha(SW.primary, 0.18)}`,
          '& .MuiDataGrid-columnHeaders': {
            backgroundColor: alpha(SW.primary, 0.08),
            borderBottom: `1px solid ${alpha(SW.primary, 0.3)}`,
          },
          '& .MuiDataGrid-columnHeaderTitle': {
            color: SW.primary,
            fontWeight: 700,
            fontSize: '0.7rem',
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
          },
          '& .MuiDataGrid-row:hover': { backgroundColor: alpha(SW.primary, 0.06) },
          '& .MuiDataGrid-row.Mui-selected': {
            backgroundColor: alpha(SW.primary, 0.12),
            '&:hover': { backgroundColor: alpha(SW.primary, 0.16) },
          },
          '& .MuiDataGrid-cell': { borderColor: SW.divider },
          '& .MuiDataGrid-footerContainer': {
            borderTop: `1px solid ${alpha(SW.primary, 0.2)}`,
          },
          '& .MuiDataGrid-columnSeparator': { color: alpha(SW.primary, 0.2) },
        },
      },
    },

    // -----------------------------------------------------------------------
    // ToggleButton
    // -----------------------------------------------------------------------
    MuiToggleButton: {
      styleOverrides: {
        root: {
          textTransform: 'none',
          fontWeight: 600,
          borderColor: alpha(SW.primary, 0.25),
          color: SW.textSecondary,
          '&.Mui-selected': {
            backgroundColor: alpha(SW.primary, 0.15),
            color: SW.primary,
            borderColor: alpha(SW.primary, 0.5),
            '&:hover': { backgroundColor: alpha(SW.primary, 0.22) },
          },
          '&:hover': {
            backgroundColor: alpha(SW.primary, 0.07),
            borderColor: alpha(SW.primary, 0.4),
          },
        },
      },
    },

    // -----------------------------------------------------------------------
    // Dialog
    // -----------------------------------------------------------------------
    MuiDialog: {
      styleOverrides: {
        paper: {
          backgroundColor: SW.paperElevated,
          border: `1px solid ${alpha(SW.primary, 0.3)}`,
          boxShadow: `0 8px 40px ${alpha(SW.primary, 0.2)}, 0 0 0 1px ${alpha(SW.primary, 0.1)}`,
        },
      },
    },
    MuiDialogTitle: {
      styleOverrides: {
        root: {
          fontFamily: '"Orbitron", "Inter", sans-serif',
          color: SW.primary,
          borderBottom: `1px solid ${alpha(SW.primary, 0.2)}`,
        },
      },
    },

    // -----------------------------------------------------------------------
    // Alert
    // -----------------------------------------------------------------------
    MuiAlert: {
      styleOverrides: {
        root: { border: '1px solid' },
        standardError: {
          borderColor: alpha(SW.error, 0.4),
          backgroundColor: alpha(SW.error, 0.1),
        },
        standardSuccess: {
          borderColor: alpha(SW.success, 0.4),
          backgroundColor: alpha(SW.success, 0.08),
        },
        standardInfo: {
          borderColor: alpha(SW.secondary, 0.4),
          backgroundColor: alpha(SW.secondary, 0.08),
        },
      },
    },

    // -----------------------------------------------------------------------
    // Tooltip
    // -----------------------------------------------------------------------
    MuiTooltip: {
      styleOverrides: {
        tooltip: {
          backgroundColor: SW.paperElevated,
          border: `1px solid ${alpha(SW.primary, 0.3)}`,
          color: SW.textPrimary,
          fontSize: '0.75rem',
        },
      },
    },

    // -----------------------------------------------------------------------
    // Divider
    // -----------------------------------------------------------------------
    MuiDivider: {
      styleOverrides: {
        root: { borderColor: SW.divider },
      },
    },

    // -----------------------------------------------------------------------
    // CssBaseline — global background + scrollbars + synthwave grid
    // -----------------------------------------------------------------------
    MuiCssBaseline: {
      styleOverrides: `
        body {
          background-color: ${SW.bg};
          background-image:
            linear-gradient(${alpha(SW.primary, 0.04)} 1px, transparent 1px),
            linear-gradient(90deg, ${alpha(SW.primary, 0.04)} 1px, transparent 1px);
          background-size: 40px 40px;
          min-height: 100vh;
        }
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: ${SW.bg}; }
        ::-webkit-scrollbar-thumb {
          background: ${alpha(SW.primary, 0.4)};
          border-radius: 3px;
        }
        ::-webkit-scrollbar-thumb:hover { background: ${alpha(SW.primary, 0.7)}; }
        code {
          background: ${alpha(SW.secondary, 0.12)};
          color: ${SW.secondary};
          padding: 1px 5px;
          border-radius: 4px;
          font-size: 0.85em;
        }
      `,
    },
  },
});

export default theme;
