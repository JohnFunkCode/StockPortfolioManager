import { Typography } from '@mui/material';

/** The small uppercase heading every Signals-tab panel opens with. */
export default function SectionHeader({ title }: { title: string }) {
  return (
    <Typography variant="subtitle2" sx={{ color: 'var(--qc-muted)', textTransform: 'uppercase', letterSpacing: 1, mb: 1 }}>
      {title}
    </Typography>
  );
}
