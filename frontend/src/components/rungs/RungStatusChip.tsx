import { Chip } from '@mui/material';

const statusConfig: Record<string, { color: 'info' | 'warning' | 'success'; label: string }> = {
  PENDING: { color: 'info', label: 'Pending' },
  ACHIEVED: { color: 'warning', label: 'Achieved' },
  EXECUTED: { color: 'success', label: 'Executed' },
};

export default function RungStatusChip({ status }: { status: string }) {
  const config = statusConfig[status] ?? { color: 'info' as const, label: status };
  return <Chip label={config.label} color={config.color} size="small" />;
}
