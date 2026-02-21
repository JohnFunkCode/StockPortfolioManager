export function formatCurrency(value: number | null | undefined): string {
  if (value == null) return 'N/A';
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
  }).format(value);
}

export function formatPercent(value: number | null | undefined, decimals = 2): string {
  if (value == null) return 'N/A';
  return `${(value * 100).toFixed(decimals)}%`;
}

export function formatPercentRaw(value: number | null | undefined, decimals = 2): string {
  if (value == null) return 'N/A';
  return `${value.toFixed(decimals)}%`;
}

export function formatDate(dateString: string | null | undefined): string {
  if (!dateString) return 'N/A';
  return new Date(dateString).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

export function formatDateTime(dateString: string | null | undefined): string {
  if (!dateString) return 'N/A';
  return new Date(dateString).toLocaleString('en-US');
}
