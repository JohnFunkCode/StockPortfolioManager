import { TextField } from '@mui/material';
import { NOTE_MAX_LENGTH } from '../../api/portfolioTypes';

interface Props {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}

/** Free-text reason for a purchase or sale (issue #266), capped like the API. */
export default function LotNoteField({ label, value, onChange, placeholder }: Props) {
  return (
    <TextField
      label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      multiline
      minRows={2}
      maxRows={8}
      fullWidth
      size="small"
      inputProps={{ maxLength: NOTE_MAX_LENGTH }}
      helperText={`${value.length} / ${NOTE_MAX_LENGTH}`}
    />
  );
}
