import { Routes, Route, Link, useLocation, Outlet } from 'react-router-dom';
import {
  AppBar, Toolbar, Typography, Button, Container, Box, Stack,
} from '@mui/material';
import DashboardIcon from '@mui/icons-material/Dashboard';
import ListAltIcon from '@mui/icons-material/ListAlt';
import ShowChartIcon from '@mui/icons-material/ShowChart';
import DashboardPage from './components/dashboard/DashboardPage';
import PlansPage from './components/plans/PlansPage';
import PlanDetailPage from './components/plans/PlanDetailPage';
import SymbolsPage from './components/symbols/SymbolsPage';

function Layout() {
  const location = useLocation();

  const navItems = [
    { label: 'Dashboard', path: '/', icon: <DashboardIcon /> },
    { label: 'Plans', path: '/plans', icon: <ListAltIcon /> },
    { label: 'Symbols', path: '/symbols', icon: <ShowChartIcon /> },
  ];

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', minHeight: '100vh' }}>
      <AppBar position="static">
        <Toolbar>
          <Typography variant="h6" sx={{ mr: 4 }}>
            Harvest Ladder
          </Typography>
          <Stack direction="row" spacing={1}>
            {navItems.map((item) => (
              <Button
                key={item.path}
                component={Link}
                to={item.path}
                color="inherit"
                startIcon={item.icon}
                sx={{
                  opacity: location.pathname === item.path ? 1 : 0.7,
                  borderBottom: location.pathname === item.path ? '2px solid white' : 'none',
                  borderRadius: 0,
                }}
              >
                {item.label}
              </Button>
            ))}
          </Stack>
        </Toolbar>
      </AppBar>
      <Container maxWidth="xl" sx={{ py: 3, flex: 1 }}>
        <Outlet />
      </Container>
    </Box>
  );
}

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/plans" element={<PlansPage />} />
        <Route path="/plans/:id" element={<PlanDetailPage />} />
        <Route path="/symbols" element={<SymbolsPage />} />
        <Route path="*" element={<Typography variant="h5" sx={{ mt: 4 }}>Page not found</Typography>} />
      </Route>
    </Routes>
  );
}
