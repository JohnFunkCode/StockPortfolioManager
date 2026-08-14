import { useEffect, useState } from 'react';
import { Routes, Route, Outlet } from 'react-router-dom';
import { Typography, Container, Box } from '@mui/material';
import ChatRail from './components/chat/ChatRail';
import NavBar from './components/layout/NavBar';
import { useChat } from './chat/ChatContext';
import RestrictedAccess from './components/access/RestrictedAccess';
import { routes } from './navigation';
import { onNotProvisioned } from './api/client';

function Layout() {
  const { railOpen, expanded } = useChat();
  const chatFullscreen = railOpen && expanded;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>
      <NavBar />
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
            // CSS coerces a lone overflowY: 'auto' into overflowX: 'auto' too
            // (the "auto -> auto" overflow rule), which put a second,
            // easy-to-miss horizontal scrollbar on this Container itself
            // whenever a page's content (e.g. the Watchlist DataGrid) ran
            // wider than the maxWidth="xl" cap — on top of the DataGrid's
            // own internal horizontal scrollbar. Pin it shut; components
            // that need horizontal scroll (like DataGrid) already manage
            // their own.
            overflowX: 'hidden',
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
