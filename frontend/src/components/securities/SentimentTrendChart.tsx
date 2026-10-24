/**
 * Weekly net news sentiment for one ticker (issue #411).
 *
 * Lives inside the Signals tab's "Recent News & Sentiment" panel and renders on
 * its own in Sidekick chat (registry key `sentiment_trend`). Self-contained:
 * the only prop is the ticker, and the 4w / 13w / 26w window is local view
 * state — it refetches, it never sends anything to the conversation.
 *
 * The server fills every week in the window and computes every number and
 * flag (`quantcore/analytics/sentiment.py`); this component only draws them.
 * Three states are kept visibly apart, because prod's news history is young
 * and has a collection outage in it:
 *   - before history  — shaded, labelled; unmeasured, not neutral;
 *   - no articles     — a dashed outline, "0 articles";
 *   - thin            — a paler bar (fewer than `thin_threshold` articles).
 */
import { useState } from 'react';
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material';
import { useSentimentTrend } from '../../hooks/useSecurities';
import type { SentimentTrendResponse, SentimentTrendWeek } from '../../api/securitiesTypes';

export interface SentimentTrendChartProps {
  ticker: string;
}

const WINDOWS = [4, 13, 26] as const;
const DEFAULT_WEEKS = 13;
const CHART_HEIGHT = 120;
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "2026-10-05" -> "Oct 05", without a Date (so no timezone can shift it). */
function shortDate(iso: string): string {
  const [, m, d] = iso.split('-');
  return `${MONTHS[Number(m) - 1]} ${d}`;
}

/** Signed to two places with a real minus sign. */
function signed(n: number): string {
  const s = Math.abs(n).toFixed(2);
  return n > 0 ? `+${s}` : n < 0 ? `−${s}` : s;
}

function barColor(week: SentimentTrendWeek): string {
  if (week.net_score == null || week.net_score === 0) return 'var(--qc-muted)';
  return week.net_score > 0 ? 'var(--qc-ink-green)' : 'var(--qc-ink-red)';
}

function weekSummary(week: SentimentTrendWeek): string {
  const head = `Week of ${shortDate(week.week_start)}`;
  if (week.before_history) return `${head}: before news history`;
  if (week.article_count === 0) return `${head}: no articles`;
  const parts = [
    `${week.article_count} articles`,
    `${week.positive_count}↑ ${week.negative_count}↓ ${week.neutral_count}–`,
    `net ${signed(week.net_score ?? 0)}`,
  ];
  if (week.thin) parts.push('thin');
  if (week.in_progress) parts.push('in progress');
  if (week.flip) parts.push(`flip: ${week.flip}`);
  return `${head}: ${parts.join(' · ')}`;
}

function chartLabel(ticker: string, data: SentimentTrendResponse): string {
  const measured = data.buckets.filter((b) => b.article_count > 0).map(weekSummary);
  const empty = data.weeks - data.weeks_with_articles;
  const tail = empty > 0 ? `; ${empty} week${empty === 1 ? '' : 's'} without articles` : '';
  return `Weekly net news sentiment for ${ticker}: ${measured.join('; ') || 'no articles'}${tail}`;
}

function coverageLine(data: SentimentTrendResponse): string {
  const history = data.history_start
    ? `history since ${shortDate(data.history_start)}`
    : 'no news history yet';
  return (
    `${data.weeks_with_articles} of ${data.weeks} weeks with articles · ` +
    `${data.total_articles} articles · ${history}`
  );
}

function WeekColumn({ week, showDate }: { week: SentimentTrendWeek; showDate: boolean }) {
  const half = CHART_HEIGHT / 2;
  const net = week.net_score ?? 0;
  // A zero-net week still gets a hairline so it reads as "measured, flat".
  const barHeight = Math.max(2, Math.abs(net) * half);
  const measured = week.article_count > 0;
  const caption = week.before_history
    ? ''
    : measured
      ? `${signed(net)} · ${week.article_count}`
      : '0 articles';

  return (
    <Tooltip title={weekSummary(week)} placement="top">
      <Box
        data-testid={`trend-week-${week.week_start}`}
        sx={{ flex: '1 1 0', minWidth: 0, display: 'flex', flexDirection: 'column', alignItems: 'center' }}
      >
        <Box
          sx={{
            position: 'relative',
            width: '100%',
            height: CHART_HEIGHT,
            // The card itself is surface-sunk, so the shade must be a step up from it.
            bgcolor: week.before_history ? 'var(--qc-surface)' : 'transparent',
          }}
        >
          {week.flip && (
            <Typography
              data-testid="trend-flip"
              aria-label={`sentiment flip: ${week.flip}`}
              sx={{
                position: 'absolute', top: 0, left: 0, right: 0, textAlign: 'center',
                fontSize: 11, fontWeight: 700, lineHeight: 1,
                color: week.flip === 'deteriorating' ? 'var(--qc-ink-red)' : 'var(--qc-ink-green)',
              }}
            >
              {week.flip === 'deteriorating' ? '▼' : '▲'}
            </Typography>
          )}
          {measured && (
            <Box
              data-testid="trend-bar"
              sx={{
                position: 'absolute',
                left: '15%',
                right: '15%',
                height: barHeight,
                ...(net >= 0 ? { bottom: half } : { top: half }),
                bgcolor: barColor(week),
                opacity: week.thin ? 0.45 : 1,
                borderRadius: 0.5,
              }}
            />
          )}
          {!measured && !week.before_history && (
            <Box
              data-testid="trend-empty"
              sx={{
                position: 'absolute', left: '15%', right: '15%', top: half - 10, height: 20,
                border: '1px dashed var(--qc-faint)', borderRadius: 0.5,
              }}
            />
          )}
          {week.in_progress && (
            <Box
              data-testid="trend-in-progress"
              sx={{
                position: 'absolute', left: '8%', right: '8%', top: 14, bottom: 14,
                border: '1px dashed var(--qc-ink-amber)', borderRadius: 0.5, pointerEvents: 'none',
              }}
            />
          )}
        </Box>
        <Typography
          variant="caption"
          sx={{
            fontSize: 10, lineHeight: 1.4, whiteSpace: 'nowrap', overflow: 'hidden',
            maxWidth: '100%', textOverflow: 'ellipsis',
            color: week.in_progress ? 'var(--qc-ink-amber)' : 'var(--qc-muted)',
          }}
        >
          {caption || ' '}
        </Typography>
        <Typography
          variant="caption"
          sx={{ fontSize: 10, lineHeight: 1.4, whiteSpace: 'nowrap', color: 'var(--qc-faint)' }}
        >
          {showDate ? shortDate(week.week_start) : ' '}
        </Typography>
      </Box>
    </Tooltip>
  );
}

function TrendBody({ ticker, data }: { ticker: string; data: SentimentTrendResponse }) {
  const buckets = data.buckets;
  // Label every week at 4w, then thin the dates out so they never collide.
  const every = Math.max(1, Math.ceil(buckets.length / 7));
  const leadingUnmeasured = buckets.findIndex((b) => !b.before_history);
  const shaded = leadingUnmeasured === -1 ? buckets.length : leadingUnmeasured;

  return (
    <>
      <Box sx={{ position: 'relative' }}>
        <Box
          role="img"
          aria-label={chartLabel(ticker, data)}
          data-testid="sentiment-trend-chart"
          // No gap: the bars are inset already, and a gap would tile the shaded run.
          sx={{ display: 'flex', position: 'relative' }}
        >
          {buckets.map((week, i) => (
            <WeekColumn key={week.week_start} week={week} showDate={i % every === 0} />
          ))}
        </Box>
        {/* Zero line across the plot, under the captions. */}
        <Box
          sx={{
            position: 'absolute', left: 0, right: 0, top: CHART_HEIGHT / 2, height: '1px',
            bgcolor: 'var(--qc-line)', pointerEvents: 'none',
          }}
        />
        {shaded >= 2 && (
          <Typography
            variant="caption"
            sx={{
              position: 'absolute', left: 0, width: `${(shaded / buckets.length) * 100}%`,
              top: CHART_HEIGHT / 2 + 4, textAlign: 'center', color: 'var(--qc-muted)',
              pointerEvents: 'none',
            }}
          >
            {data.history_start
              ? `No news history before ${shortDate(data.history_start)}`
              : 'No news history yet'}
          </Typography>
        )}
      </Box>
      <Typography variant="caption" sx={{ color: 'var(--qc-muted)', display: 'block', mt: 0.5 }}>
        bar = (positive − negative) ÷ articles · paler = fewer than {data.thin_threshold} articles ·
        dashed = no articles that week · ▲▼ = sentiment flip
      </Typography>
    </>
  );
}

export default function SentimentTrendChart({ ticker }: SentimentTrendChartProps) {
  const [weeks, setWeeks] = useState<number>(DEFAULT_WEEKS);
  const { data, isLoading, error, refetch, isFetching } = useSentimentTrend(ticker, weeks);

  let body;
  if (isLoading) {
    body = (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
        <CircularProgress size={22} />
      </Box>
    );
  } else if (error || !data?.buckets) {
    body = (
      <Alert
        severity="error"
        action={
          <Button color="inherit" size="small" disabled={isFetching} onClick={() => refetch()}>
            Retry
          </Button>
        }
      >
        Couldn&apos;t load the sentiment trend.
      </Alert>
    );
  } else {
    body = <TrendBody ticker={ticker} data={data} />;
  }

  return (
    <Box
      data-testid="sentiment-trend"
      sx={{
        border: '1px solid var(--qc-line)', borderRadius: 1, p: 1.5,
        bgcolor: 'var(--qc-surface-sunk)', display: 'flex', flexDirection: 'column', gap: 1,
      }}
    >
      <Stack direction="row" justifyContent="space-between" alignItems="center" flexWrap="wrap" useFlexGap gap={1}>
        <Typography variant="caption" sx={{ color: 'var(--qc-text-soft)', fontWeight: 600 }}>
          Weekly net sentiment
          {data?.buckets && (
            <Box component="span" sx={{ color: 'var(--qc-muted)', fontWeight: 400 }}>
              {' · '}
              {coverageLine(data)}
            </Box>
          )}
        </Typography>
        <ToggleButtonGroup
          size="small"
          exclusive
          value={weeks}
          aria-label="Trend window"
          onChange={(_, next: number | null) => {
            if (next != null) setWeeks(next);
          }}
        >
          {WINDOWS.map((w) => (
            <ToggleButton key={w} value={w} sx={{ py: 0.25, px: 1, fontSize: 11 }}>
              {w}w
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      </Stack>
      {body}
    </Box>
  );
}
