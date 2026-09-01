/**
 * The QuantUI top bar: wordmark, navigation, and the Sidekick/theme toggles.
 *
 * Lives here rather than inside `App.tsx` because the grouped nav (issue #147
 * Part G2) needs one menu-anchor element per group, and `App.tsx` should not
 * grow a `useState` per group to hold it.
 */
import { useState, type MouseEvent } from 'react';
import { Link, useLocation } from 'react-router-dom';
import {
  AppBar, Toolbar, Typography, Button, Box, Stack, alpha,
  IconButton, Tooltip, Menu, MenuItem, ListItemIcon, ListItemText, ListSubheader,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import MenuIcon from '@mui/icons-material/Menu';
import LightModeIcon from '@mui/icons-material/LightMode';
import DarkModeIcon from '@mui/icons-material/DarkMode';
import ChatIcon from '@mui/icons-material/Chat';
import { useTheme } from '@mui/material/styles';
import useMediaQuery from '@mui/material/useMediaQuery';
import { useChat } from '../../chat/ChatContext';
import { isPathActive, navSections, type NavRoute, type NavSection } from '../../navigation';
import { useAppTheme } from '../../ThemeContext';

/** The neon treatment the flat bar has always given the current page. */
function navButtonSx(active: boolean, isLight: boolean) {
  return {
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
  };
}

function NavGroup({
  label, items, pathname, isLight,
}: {
  label: string;
  items: NavRoute[];
  pathname: string;
  isLight: boolean;
}) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const open = anchor !== null;
  // The group lights up when any page beneath it is the current one.
  const active = items.some((item) => isPathActive(item.path, pathname));

  return (
    <>
      <Button
        color="inherit"
        endIcon={<ExpandMoreIcon />}
        onClick={(event: MouseEvent<HTMLElement>) => setAnchor(event.currentTarget)}
        aria-haspopup="true"
        aria-expanded={open}
        // Not `page` — the trigger is not itself the page, it is the current
        // one of a set of nav controls. The MenuItem inside claims `page`.
        aria-current={active ? true : undefined}
        sx={navButtonSx(active, isLight)}
      >
        {label}
      </Button>
      <Menu anchorEl={anchor} open={open} onClose={() => setAnchor(null)}>
        {items.map((item) => {
          const current = isPathActive(item.path, pathname);
          return (
          <MenuItem
            key={item.path}
            component={Link}
            to={item.path}
            selected={current}
            aria-current={current ? 'page' : undefined}
            // Without this the popover stays open over the page it navigated to.
            onClick={() => setAnchor(null)}
          >
            <ListItemIcon sx={{ color: 'inherit' }}>{item.icon}</ListItemIcon>
            <ListItemText>{item.label}</ListItemText>
          </MenuItem>
          );
        })}
      </Menu>
    </>
  );
}

function MobileNavMenu({ sections, pathname }: { sections: NavSection[]; pathname: string }) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const open = anchor !== null;

  return (
    <>
      <IconButton
        color="inherit"
        aria-label="Open navigation"
        aria-haspopup="true"
        aria-expanded={open}
        data-testid="mobile-nav-menu"
        onClick={(event) => setAnchor(event.currentTarget)}
        sx={{
          color: 'primary.main',
          border: '1px solid',
          borderColor: (theme) => alpha(theme.palette.primary.main, 0.35),
          borderRadius: 1.5,
          p: 0.75,
        }}
      >
        <MenuIcon fontSize="small" />
      </IconButton>
      <Menu anchorEl={anchor} open={open} onClose={() => setAnchor(null)}>
        {sections.flatMap((section) => {
          if (section.kind === 'item') {
            const current = isPathActive(section.route.path, pathname);
            return [
              <MenuItem
                key={section.route.path}
                component={Link}
                to={section.route.path}
                selected={current}
                aria-current={current ? 'page' : undefined}
                onClick={() => setAnchor(null)}
              >
                <ListItemIcon sx={{ color: 'inherit' }}>{section.route.icon}</ListItemIcon>
                <ListItemText>{section.route.label}</ListItemText>
              </MenuItem>,
            ];
          }

          return [
            <ListSubheader key={`${section.label}-header`}>{section.label}</ListSubheader>,
            ...section.items.map((item) => {
              const current = isPathActive(item.path, pathname);
              return (
                <MenuItem
                  key={item.path}
                  component={Link}
                  to={item.path}
                  selected={current}
                  aria-current={current ? 'page' : undefined}
                  onClick={() => setAnchor(null)}
                >
                  <ListItemIcon sx={{ color: 'inherit' }}>{item.icon}</ListItemIcon>
                  <ListItemText>{item.label}</ListItemText>
                </MenuItem>
              );
            }),
          ];
        })}
      </Menu>
    </>
  );
}

export default function NavBar() {
  const location = useLocation();
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down('sm'));
  const { themeName, setThemeName } = useAppTheme();
  const { railOpen, setRailOpen } = useChat();
  const isLight = themeName === 'light';

  return (
    <AppBar position="static">
      <Toolbar>
        <Typography
          variant="h6"
          sx={{
            mr: { xs: 1, sm: 4 },
            fontSize: { xs: '1rem', sm: '1.25rem' },
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

        {isMobile ? (
          <MobileNavMenu sections={navSections} pathname={location.pathname} />
        ) : (
          <Stack direction="row" spacing={1}>
            {navSections.map((section) => (
              section.kind === 'group' ? (
                <NavGroup
                  key={section.label}
                  label={section.label}
                  items={section.items}
                  pathname={location.pathname}
                  isLight={isLight}
                />
              ) : (
                <Button
                  key={section.route.path}
                  component={Link}
                  to={section.route.path}
                  color="inherit"
                  startIcon={section.route.icon}
                  aria-current={
                    isPathActive(section.route.path, location.pathname) ? 'page' : undefined
                  }
                  sx={navButtonSx(isPathActive(section.route.path, location.pathname), isLight)}
                >
                  {section.route.label}
                </Button>
              )
            ))}
          </Stack>
        )}

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
  );
}
