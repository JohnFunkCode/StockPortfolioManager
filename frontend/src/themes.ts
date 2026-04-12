import { createTheme, alpha } from '@mui/material/styles';
import type {} from '@mui/x-data-grid/themeAugmentation';

// ---------------------------------------------------------------------------
// Palette definitions
// ---------------------------------------------------------------------------

const DARK = {
  bg:            '#0a0015',
  paper:         '#130926',
  paperElevated: '#1c0f35',
  primary:       '#ff2d78',
  primaryDark:   '#c4005a',
  secondary:     '#00e5ff',
  secondaryDark: '#009ab5',
  success:       '#00e676',
  warning:       '#ff9100',
  error:         '#ff3366',
  info:          '#e040fb',
  textPrimary:   '#f0e6ff',
  textSecondary: '#b39ddb',
  divider:       '#2a0f4a',
  appBarBg:      'linear-gradient(135deg, #0d0020 0%, #0a0015 100%)',
};

// Light Synthwave — neon accents on a pale lavender canvas
const LIGHT = {
  bg:            '#fdf5ff',
  paper:         '#ffffff',
  paperElevated: '#f5ebff',
  primary:       '#c4005a',   // deep neon pink — readable on white
  primaryDark:   '#8c0041',
  secondary:     '#0097a7',   // dark cyan — readable on white
  secondaryDark: '#006978',
  success:       '#1b7a45',
  warning:       '#b85c00',
  error:         '#b0002a',
  info:          '#7b00c2',
  textPrimary:   '#1a0030',   // near-black purple
  textSecondary: '#7b3fc0',   // mid purple
  divider:       '#e8d5f5',
  appBarBg:      'linear-gradient(135deg, #fff0fc 0%, #fdf5ff 100%)',
};

// ---------------------------------------------------------------------------
// Factory
// ---------------------------------------------------------------------------

function buildTheme(P: typeof DARK, mode: 'dark' | 'light') {
  const glowStr = mode === 'dark' ? 0.6 : 0.35;
  const glowDif = mode === 'dark' ? 0.25 : 0.12;
  const glowP = `0 0 8px ${alpha(P.primary,   glowStr)}, 0 0 20px ${alpha(P.primary,   glowDif)}`;
  const glowC = `0 0 8px ${alpha(P.secondary, glowStr)}, 0 0 20px ${alpha(P.secondary, glowDif)}`;
  const glowG = `0 0 8px ${alpha(P.success,   glowStr)}, 0 0 16px ${alpha(P.success,   glowDif)}`;

  return createTheme({
    palette: {
      mode,
      primary:    { main: P.primary,    dark: P.primaryDark,   contrastText: mode === 'dark' ? '#fff' : '#fff' },
      secondary:  { main: P.secondary,  dark: P.secondaryDark, contrastText: '#000' },
      success:    { main: P.success },
      warning:    { main: P.warning },
      error:      { main: P.error   },
      info:       { main: P.info    },
      background: { default: P.bg, paper: P.paper },
      text:       { primary: P.textPrimary, secondary: P.textSecondary },
      divider:    P.divider,
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
      MuiAppBar: {
        styleOverrides: {
          root: {
            background: P.appBarBg,
            boxShadow: `0 1px 0 0 ${P.divider}, 0 2px 16px 0 ${alpha(P.primary, 0.15)}`,
            borderBottom: `1px solid ${alpha(P.primary, 0.3)}`,
          },
        },
      },

      MuiPaper: {
        styleOverrides: {
          root: {
            backgroundImage: 'none',
            backgroundColor: P.paper,
            border: `1px solid ${alpha(P.primary, 0.12)}`,
          },
          elevation2: { backgroundColor: P.paperElevated },
          elevation3: { backgroundColor: P.paperElevated },
        },
      },

      MuiCard: {
        styleOverrides: {
          root: {
            backgroundColor: P.paper,
            border: `1px solid ${alpha(P.primary, 0.18)}`,
            transition: 'border-color 0.2s, box-shadow 0.2s',
            '&:hover': {
              borderColor: alpha(P.primary, 0.45),
              boxShadow: glowP,
            },
          },
        },
      },

      MuiButton: {
        styleOverrides: {
          root: {
            textTransform: 'none',
            fontWeight: 600,
            letterSpacing: '0.03em',
            transition: 'box-shadow 0.2s, transform 0.1s',
          },
          containedPrimary: {
            background: `linear-gradient(135deg, ${P.primary} 0%, ${P.primaryDark} 100%)`,
            boxShadow: `0 2px 12px ${alpha(P.primary, 0.4)}`,
            '&:hover': {
              background: `linear-gradient(135deg, ${alpha(P.primary, 0.85)} 0%, ${P.primary} 100%)`,
              boxShadow: glowP,
              transform: 'translateY(-1px)',
            },
          },
          containedSecondary: {
            background: `linear-gradient(135deg, ${P.secondary} 0%, ${P.secondaryDark} 100%)`,
            color: '#000',
            boxShadow: `0 2px 12px ${alpha(P.secondary, 0.35)}`,
            '&:hover': {
              background: `linear-gradient(135deg, ${alpha(P.secondary, 0.85)} 0%, ${P.secondary} 100%)`,
              boxShadow: glowC,
              transform: 'translateY(-1px)',
            },
          },
          outlinedPrimary: {
            borderColor: alpha(P.primary, 0.6),
            color: P.primary,
            '&:hover': {
              borderColor: P.primary,
              backgroundColor: alpha(P.primary, 0.08),
              boxShadow: `0 0 10px ${alpha(P.primary, 0.3)}`,
            },
          },
          outlinedSecondary: {
            borderColor: alpha(P.secondary, 0.6),
            color: P.secondary,
            '&:hover': {
              borderColor: P.secondary,
              backgroundColor: alpha(P.secondary, 0.08),
              boxShadow: `0 0 10px ${alpha(P.secondary, 0.3)}`,
            },
          },
        },
      },

      MuiChip: {
        styleOverrides: {
          root: { fontWeight: 600 },
          colorPrimary: {
            backgroundColor: alpha(P.primary, 0.15),
            color: P.primary,
            border: `1px solid ${alpha(P.primary, 0.4)}`,
          },
          colorSecondary: {
            backgroundColor: alpha(P.secondary, 0.12),
            color: P.secondary,
            border: `1px solid ${alpha(P.secondary, 0.4)}`,
          },
          colorSuccess: {
            backgroundColor: alpha(P.success, 0.12),
            color: P.success,
            border: `1px solid ${alpha(P.success, 0.4)}`,
          },
          colorError: {
            backgroundColor: alpha(P.error, 0.12),
            color: P.error,
            border: `1px solid ${alpha(P.error, 0.4)}`,
          },
          colorInfo: {
            backgroundColor: alpha(P.info, 0.12),
            color: P.info,
            border: `1px solid ${alpha(P.info, 0.4)}`,
          },
        },
      },

      MuiTabs: {
        styleOverrides: {
          indicator: {
            backgroundColor: P.primary,
            boxShadow: `0 0 8px ${alpha(P.primary, 0.8)}`,
            height: 2,
          },
        },
      },
      MuiTab: {
        styleOverrides: {
          root: {
            textTransform: 'none',
            fontWeight: 600,
            color: P.textSecondary,
            '&.Mui-selected': { color: P.primary },
            '&:hover': { color: alpha(P.primary, 0.8) },
          },
        },
      },

      MuiOutlinedInput: {
        styleOverrides: {
          root: {
            '& .MuiOutlinedInput-notchedOutline': {
              borderColor: alpha(P.primary, 0.25),
            },
            '&:hover .MuiOutlinedInput-notchedOutline': {
              borderColor: alpha(P.primary, 0.55),
            },
            '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
              borderColor: P.primary,
              boxShadow: `0 0 0 2px ${alpha(P.primary, 0.15)}`,
            },
          },
        },
      },
      MuiInputLabel: {
        styleOverrides: {
          root: {
            color: P.textSecondary,
            '&.Mui-focused': { color: P.primary },
          },
        },
      },
      MuiSelect: {
        styleOverrides: {
          icon: { color: P.textSecondary },
        },
      },

      MuiTableHead: {
        styleOverrides: {
          root: {
            '& .MuiTableCell-head': {
              backgroundColor: alpha(P.primary, 0.08),
              color: P.primary,
              fontWeight: 700,
              borderBottom: `1px solid ${alpha(P.primary, 0.3)}`,
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
            '&:hover': { backgroundColor: alpha(P.primary, 0.05) },
            '& .MuiTableCell-root': { borderBottom: `1px solid ${P.divider}` },
          },
        },
      },

      MuiDataGrid: {
        styleOverrides: {
          root: {
            border: `1px solid ${alpha(P.primary, 0.18)}`,
            '& .MuiDataGrid-columnHeaders': {
              backgroundColor: alpha(P.primary, 0.08),
              borderBottom: `1px solid ${alpha(P.primary, 0.3)}`,
            },
            '& .MuiDataGrid-columnHeaderTitle': {
              color: P.primary,
              fontWeight: 700,
              fontSize: '0.7rem',
              textTransform: 'uppercase',
              letterSpacing: '0.06em',
            },
            '& .MuiDataGrid-row:hover': { backgroundColor: alpha(P.primary, 0.06) },
            '& .MuiDataGrid-row.Mui-selected': {
              backgroundColor: alpha(P.primary, 0.12),
              '&:hover': { backgroundColor: alpha(P.primary, 0.16) },
            },
            '& .MuiDataGrid-cell': { borderColor: P.divider },
            '& .MuiDataGrid-footerContainer': {
              borderTop: `1px solid ${alpha(P.primary, 0.2)}`,
            },
            '& .MuiDataGrid-columnSeparator': { color: alpha(P.primary, 0.2) },
          },
        },
      },

      MuiToggleButton: {
        styleOverrides: {
          root: {
            textTransform: 'none',
            fontWeight: 600,
            borderColor: alpha(P.primary, 0.25),
            color: P.textSecondary,
            '&.Mui-selected': {
              backgroundColor: alpha(P.primary, 0.15),
              color: P.primary,
              borderColor: alpha(P.primary, 0.5),
              '&:hover': { backgroundColor: alpha(P.primary, 0.22) },
            },
            '&:hover': {
              backgroundColor: alpha(P.primary, 0.07),
              borderColor: alpha(P.primary, 0.4),
            },
          },
        },
      },

      MuiDialog: {
        styleOverrides: {
          paper: {
            backgroundColor: P.paperElevated,
            border: `1px solid ${alpha(P.primary, 0.3)}`,
            boxShadow: `0 8px 40px ${alpha(P.primary, 0.2)}, 0 0 0 1px ${alpha(P.primary, 0.1)}`,
          },
        },
      },
      MuiDialogTitle: {
        styleOverrides: {
          root: {
            fontFamily: '"Orbitron", "Inter", sans-serif',
            color: P.primary,
            borderBottom: `1px solid ${alpha(P.primary, 0.2)}`,
          },
        },
      },

      MuiAlert: {
        styleOverrides: {
          root: { border: '1px solid' },
          standardError: {
            borderColor: alpha(P.error, 0.4),
            backgroundColor: alpha(P.error, 0.1),
          },
          standardSuccess: {
            borderColor: alpha(P.success, 0.4),
            backgroundColor: alpha(P.success, 0.08),
          },
          standardInfo: {
            borderColor: alpha(P.secondary, 0.4),
            backgroundColor: alpha(P.secondary, 0.08),
          },
        },
      },

      MuiTooltip: {
        styleOverrides: {
          tooltip: {
            backgroundColor: P.paperElevated,
            border: `1px solid ${alpha(P.primary, 0.3)}`,
            color: P.textPrimary,
            fontSize: '0.75rem',
          },
        },
      },

      MuiDivider: {
        styleOverrides: {
          root: { borderColor: P.divider },
        },
      },

      MuiCssBaseline: {
        styleOverrides: `
          body {
            background-color: ${P.bg};
            background-image:
              linear-gradient(${alpha(P.primary, mode === 'dark' ? 0.04 : 0.03)} 1px, transparent 1px),
              linear-gradient(90deg, ${alpha(P.primary, mode === 'dark' ? 0.04 : 0.03)} 1px, transparent 1px);
            background-size: 40px 40px;
            min-height: 100vh;
          }
          ::-webkit-scrollbar { width: 6px; height: 6px; }
          ::-webkit-scrollbar-track { background: ${P.bg}; }
          ::-webkit-scrollbar-thumb {
            background: ${alpha(P.primary, 0.4)};
            border-radius: 3px;
          }
          ::-webkit-scrollbar-thumb:hover { background: ${alpha(P.primary, 0.7)}; }
          code {
            background: ${alpha(P.secondary, 0.12)};
            color: ${P.secondary};
            padding: 1px 5px;
            border-radius: 4px;
            font-size: 0.85em;
          }
        `,
      },
    },
  });
}

// ---------------------------------------------------------------------------
// Exported themes
// ---------------------------------------------------------------------------

export type ThemeName = 'dark' | 'light';

export const darkTheme  = buildTheme(DARK,  'dark');
export const lightTheme = buildTheme(LIGHT, 'light');

export const themes: Record<ThemeName, ReturnType<typeof buildTheme>> = {
  dark:  darkTheme,
  light: lightTheme,
};

// Dark glow helpers (kept for any component that imports them)
export const glowPink  = `0 0 8px ${alpha(DARK.primary,   0.6)}, 0 0 20px ${alpha(DARK.primary,   0.25)}`;
export const glowCyan  = `0 0 8px ${alpha(DARK.secondary, 0.6)}, 0 0 20px ${alpha(DARK.secondary, 0.25)}`;
export const glowGreen = `0 0 8px ${alpha(DARK.success,   0.55)}, 0 0 16px ${alpha(DARK.success,  0.2)}`;

export default darkTheme;
