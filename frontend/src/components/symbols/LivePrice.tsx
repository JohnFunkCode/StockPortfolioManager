import { Chip, CircularProgress } from '@mui/material';
import AttachMoneyIcon from '@mui/icons-material/AttachMoney';
import { usePricePolling } from '../../hooks/useSymbols';
import { formatCurrency } from '../../utils/formatting';

export default function LivePrice({ ticker }: { ticker: string }) {
  const { data, isLoading, isError } = usePricePolling(ticker);

  if (isLoading) return <CircularProgress size={16} />;
  if (isError || !data) return <Chip label="Price unavailable" size="small" color="default" />;

  return (
    <Chip
      icon={<AttachMoneyIcon />}
      label={`${ticker} ${formatCurrency(data.price)}`}
      color="success"
      variant="outlined"
      size="small"
    />
  );
}
