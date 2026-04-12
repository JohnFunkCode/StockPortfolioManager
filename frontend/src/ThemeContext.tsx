import { createContext, useContext, useState, useMemo } from 'react';
import type { ReactNode } from 'react';
import { ThemeProvider } from '@mui/material/styles';
import CssBaseline from '@mui/material/CssBaseline';
import { themes } from './themes';
import type { ThemeName } from './themes';

const STORAGE_KEY = 'hl-theme';

interface ThemeCtx {
  themeName: ThemeName;
  setThemeName: (name: ThemeName) => void;
}

const ThemeContext = createContext<ThemeCtx>({
  themeName: 'dark',
  setThemeName: () => {},
});

export function useAppTheme() {
  return useContext(ThemeContext);
}

export function AppThemeProvider({ children }: { children: ReactNode }) {
  const [themeName, setThemeNameState] = useState<ThemeName>(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored === 'light' ? 'light' : 'dark';
  });

  const setThemeName = (name: ThemeName) => {
    localStorage.setItem(STORAGE_KEY, name);
    setThemeNameState(name);
  };

  const ctx = useMemo(() => ({ themeName, setThemeName }), [themeName]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <ThemeContext.Provider value={ctx}>
      <ThemeProvider theme={themes[themeName]}>
        <CssBaseline />
        {children}
      </ThemeProvider>
    </ThemeContext.Provider>
  );
}
