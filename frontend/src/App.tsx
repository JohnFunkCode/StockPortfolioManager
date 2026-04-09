import { Routes, Route, Link, useLocation, Outlet } from 'react-router-dom';
import {
  AppBar, Toolbar, Typography, Button, Container, Box, Stack, alpha,
} from '@mui/material';
import DashboardIcon from '@mui/icons-material/Dashboard';
import ListAltIcon from '@mui/icons-material/ListAlt';
import ShowChartIcon from '@mui/icons-material/ShowChart';
import BarChartIcon from '@mui/icons-material/BarChart';
import DashboardPage from './components/dashboard/DashboardPage';
import PlansPage from './components/plans/PlansPage';
import PlanDetailPage from './components/plans/PlanDetailPage';
import SymbolsPage from './components/symbols/SymbolsPage';
import SecuritiesPage from './components/securities/SecuritiesPage';
import SecurityDetailPage from './components/securities/SecurityDetailPage';

function Layout() {
  const location = useLocation();

  const navItems = [
    { label: 'Dashboard', path: '/', icon: <DashboardIcon /> },
    { label: 'Securities', path: '/securities', icon: <BarChartIcon /> },
    { label: 'Plans', path: '/plans', icon: <ListAltIcon /> },
    { label: 'Symbols', path: '/symbols', icon: <ShowChartIcon /> },
  ];

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', minHeight: '100vh' }}>
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
            Harvest Ladder
          </Typography>
          <Stack direction="row" spacing={1}>
            {navItems.map((item) => {
              const active = location.pathname === item.path ||
                (item.path !== '/' && location.pathname.startsWith(item.path));
              return (
                <Button
                  key={item.path}
                  component={Link}
                  to={item.path}
                  color="inherit"
                  startIcon={item.icon}
                  sx={{
                    color: active ? '#ff2d78' : 'rgba(240,230,255,0.7)',
                    borderBottom: active ? '2px solid #ff2d78' : '2px solid transparent',
                    borderRadius: 0,
                    fontWeight: active ? 700 : 500,
                    textShadow: active ? '0 0 12px rgba(255,45,120,0.7)' : 'none',
                    transition: 'color 0.2s, text-shadow 0.2s',
                    '&:hover': {
                      color: '#ff2d78',
                      backgroundColor: alpha('#ff2d78', 0.07),
                      textShadow: '0 0 12px rgba(255,45,120,0.5)',
                    },
                  }}
                >
                  {item.label}
                </Button>
              );
            })}
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
        <Route path="/securities" element={<SecuritiesPage />} />
        <Route path="/securities/:symbol" element={<SecurityDetailPage />} />
        <Route path="/plans" element={<PlansPage />} />
        <Route path="/plans/:id" element={<PlanDetailPage />} />
        <Route path="/symbols" element={<SymbolsPage />} />
        <Route path="*" element={<Typography variant="h5" sx={{ mt: 4 }}>Page not found</Typography>} />
      </Route>
    </Routes>
  );
}
