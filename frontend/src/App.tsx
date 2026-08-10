import { useEffect, useState } from 'react';
import { Routes, Route, Link, useLocation, Outlet } from 'react-router-dom';
import {
  AppBar, Toolbar, Typography, Button, Container, Box, Stack, alpha,
  IconButton, Tooltip,
} from '@mui/material';
import LightModeIcon from '@mui/icons-material/LightMode';
import DarkModeIcon from '@mui/icons-material/DarkMode';
import ChatIcon from '@mui/icons-material/Chat';
import ChatRail from './components/chat/ChatRail';
import { useChat } from './chat/ChatContext';
import RestrictedAccess from './components/access/RestrictedAccess';
import { isPathActive, navItems, routes } from './navigation';
import { useAppTheme } from './ThemeContext';
import { onNotProvisioned } from './api/client';

function Layout() {
  const location = useLocation();
  const { themeName, setThemeName } = useAppTheme();
  const { railOpen, setRailOpen, expanded } = useChat();
  const chatFullscreen = railOpen && expanded;
  const isLight = themeName === 'light';

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>
      <AppBar position="static">
        <Toolbar>
          <Typography
            variant="h6"
            sx={{
              mr: 4,
              fontFamily: '"Orbitron", sans-serif',
              fontWeight: 700,
              background: 'linear-gradient(90deg, #ff2d78 0%, #00e5ff 100%)',
              WebkitBackgroundClip: 'text',
              WebkitTextFillColor: 'transparent',
              backgroundClip: 'text',
              letterSpacing: '0.05em',
            }}
          >
            QuantUI
          </Typography>

          <Stack direction="row" spacing={1}>
            {navItems.map((item) => {
              const active = isPathActive(item.path, location.pathname);
              return (
                <Button
                  key={item.path}
                  component={Link}
                  to={item.path}
                  color="inherit"
                  startIcon={item.icon}
                  sx={{
                    color: active
                      ? 'primary.main'
                      : isLight ? 'text.secondary' : 'rgba(240,230,255,0.7)',
                    borderBottom: active ? '2px solid' : '2px solid transparent',
                    borderBottomColor: active ? 'primary.main' : 'transparent',
                    borderRadius: 0,
                    fontWeight: active ? 700 : 500,
                    textShadow: active
                      ? (theme: { palette: { primary: { main: string } } }) =>
                          `0 0 12px ${alpha(theme.palette.primary.main, 0.7)}`
                      : 'none',
                    transition: 'color 0.2s, text-shadow 0.2s',
                    '&:hover': {
                      color: 'primary.main',
                      backgroundColor: (theme: { palette: { primary: { main: string } } }) =>
                        alpha(theme.palette.primary.main, 0.07),
                      textShadow: (theme: { palette: { primary: { main: string } } }) =>
                        `0 0 12px ${alpha(theme.palette.primary.main, 0.5)}`,
                    },
                  }}
                >
                  {item.label}
                </Button>
              );
            })}
          </Stack>

          {/* Sidekick + theme toggles — pushed to the far right */}
          <Box sx={{ ml: 'auto', display: 'flex', gap: 1 }}>
            <Tooltip title={railOpen ? 'Hide Sidekick' : 'Show Sidekick'}>
              <IconButton
                onClick={() => setRailOpen(!railOpen)}
                size="small"
                data-testid="chat-toggle"
                sx={{
                  color: railOpen ? 'secondary.main' : 'primary.main',
                  border: '1px solid',
                  borderColor: (theme) => alpha(theme.palette.primary.main, 0.35),
                  borderRadius: 1.5,
                  p: 0.75,
                }}
              >
                <ChatIcon fontSize="small" />
              </IconButton>
            </Tooltip>
            <Tooltip title={isLight ? 'Switch to Dark Synthwave' : 'Switch to Light Synthwave'}>
              <IconButton
                onClick={() => setThemeName(isLight ? 'dark' : 'light')}
                size="small"
                sx={{
                  color: 'primary.main',
                  border: '1px solid',
                  borderColor: (theme) => alpha(theme.palette.primary.main, 0.35),
                  borderRadius: 1.5,
                  p: 0.75,
                  transition: 'box-shadow 0.2s, border-color 0.2s',
                  '&:hover': {
                    borderColor: 'primary.main',
                    boxShadow: (theme) =>
                      `0 0 10px ${alpha(theme.palette.primary.main, 0.4)}`,
                  },
                }}
              >
                {isLight ? <DarkModeIcon fontSize="small" /> : <LightModeIcon fontSize="small" />}
              </IconButton>
            </Tooltip>
          </Box>
        </Toolbar>
      </AppBar>
      <Box sx={{ display: 'flex', flex: 1, minHeight: 0, alignItems: 'stretch' }}>
        {/* Hidden (not unmounted) in chat fullscreen so page state survives. */}
        <Container
          maxWidth="xl"
          data-testid="page-content"
          sx={{
            py: 3,
            flex: 1,
            minWidth: 0,
            minHeight: 0,
            overflowY: 'auto',
            display: chatFullscreen ? 'none' : 'block',
          }}
        >
          <Outlet />
        </Container>
        {railOpen && <ChatRail />}
      </Box>
    </Box>
  );
}

export default function App() {
  // Issue #126 decision #2/#4: an unmapped principal must land on a full-page
  // restricted screen with no header/nav/ChatRail, never inside Layout.
  const [restricted, setRestricted] = useState(false);

  useEffect(() => onNotProvisioned(() => setRestricted(true)), []);

  if (restricted) {
    return <RestrictedAccess />;
  }

  return (
    <Routes>
      <Route element={<Layout />}>
        {routes.map((route) => (
          <Route key={route.path} path={route.path} element={route.element} />
        ))}
        <Route path="*" element={<Typography variant="h5" sx={{ mt: 4 }}>Page not found</Typography>} />
      </Route>
    </Routes>
  );
}
