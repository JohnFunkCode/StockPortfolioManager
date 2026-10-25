/**
 * The Signals tab's "Recent News & Sentiment" panel, lifted out of SignalsTab
 * (#439 review: that component's single render unit had passed CC 80).
 *
 * Order: Overall row, the weekly trend, then the article list. The trend reads
 * stored history (<1 s) while `/news` scores live headlines (64 s for WDC on
 * prod), so only the article list waits behind a spinner.
 *
 * `useNews` here and in SignalsTab's summary line share one react-query entry,
 * so lifting the panel adds no request.
 */
import { Box, Button, Chip, CircularProgress, Divider, Link, Paper, Stack, Typography } from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import { useNews } from '../../hooks/useSecurities';
import type { NewsArticle, NewsSentimentSummary } from '../../api/securitiesTypes';
import SectionHeader from './SectionHeader';
import SentimentTrendChart from './SentimentTrendChart';

// On a tint background the status colour is the --qc-ink-* shade (themes.ts).
// `fg` is the Overall chip's text, `articleFg` an article chip's: the two
// differed for neutral before the panel was lifted out, and still do.
interface Tone { bg: string; fg: string; articleFg: string; border: string; arrow: string }
const TONE: Record<string, Tone> = {
  positive: {
    bg: 'var(--qc-tint-green)', fg: 'var(--qc-ink-green)', articleFg: 'var(--qc-ink-green)',
    border: 'var(--qc-ink-green)', arrow: '↑',
  },
  negative: {
    bg: 'var(--qc-tint-red)', fg: 'var(--qc-ink-red)', articleFg: 'var(--qc-ink-red)',
    border: 'var(--qc-ink-red)', arrow: '↓',
  },
};
const NEUTRAL: Tone = {
  bg: 'var(--qc-surface)', fg: 'var(--qc-muted)', articleFg: 'var(--qc-faint)',
  border: 'var(--qc-dim)', arrow: '–',
};

function tone(sentiment: string | null | undefined): Tone {
  return TONE[sentiment ?? ''] ?? NEUTRAL;
}

function OverallRow({ summary }: { summary: NewsSentimentSummary }) {
  const t = tone(summary.overall);
  return (
    <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1.5 }}>
      <Typography variant="caption" sx={{ color: 'var(--qc-faint)' }}>Overall:</Typography>
      <Chip
        size="small"
        label={summary.overall.toUpperCase()}
        sx={{ fontSize: 11, height: 20, fontWeight: 700, bgcolor: t.bg, color: t.fg, border: '1px solid', borderColor: t.border }}
      />
      <Typography variant="caption" sx={{ color: 'var(--qc-faint)' }}>
        {summary.positive_count}↑ &nbsp;
        {summary.negative_count}↓ &nbsp;
        {summary.neutral_count}–
      </Typography>
    </Stack>
  );
}

function ArticleRow({ article }: { article: NewsArticle }) {
  const t = tone(article.sentiment);
  return (
    <Stack direction="row" spacing={1} alignItems="flex-start">
      {article.sentiment && (
        <Chip
          size="small"
          label={t.arrow}
          title={`${article.sentiment} (${((article.sentiment_score ?? 0) * 100).toFixed(0)}%)`}
          sx={{
            fontSize: 12, height: 20, minWidth: 24, mt: 0.2, flexShrink: 0,
            bgcolor: t.bg, color: t.articleFg,
          }}
        />
      )}
      <Box sx={{ flex: 1, minWidth: 0 }}>
        {article.url ? (
          <Link href={article.url} target="_blank" rel="noopener noreferrer"
            sx={{ color: 'var(--qc-text)', fontSize: 12, fontWeight: 500, textDecoration: 'none',
              '&:hover': { textDecoration: 'underline' } }}>
            {article.title}
          </Link>
        ) : (
          <Typography variant="body2" sx={{ fontSize: 12, fontWeight: 500 }}>{article.title}</Typography>
        )}
        <Typography variant="caption" sx={{ color: 'var(--qc-faint)', display: 'block', mt: 0.25 }}>
          {article.publisher}{article.published ? ` · ${new Date(article.published).toLocaleDateString()}` : ''}
          {article.sentiment_score != null && (
            <span style={{ marginLeft: 6, color: 'var(--qc-dim)' }}>
              ({(article.sentiment_score * 100).toFixed(0)}% confidence)
            </span>
          )}
        </Typography>
      </Box>
    </Stack>
  );
}

function ArticleList({ articles }: { articles: NewsArticle[] }) {
  return (
    <Stack spacing={0}>
      {articles.map((article, i) => (
        <Box key={i}>
          {i > 0 && <Divider sx={{ my: 1, borderColor: 'var(--qc-surface)' }} />}
          <ArticleRow article={article} />
        </Box>
      ))}
    </Stack>
  );
}

export default function NewsSentimentPanel({ ticker }: { ticker: string }) {
  const { data: news, isLoading, refetch } = useNews(ticker);
  const articles = news?.articles ?? [];
  // As before #439: the Overall row belongs with the articles it summarises.
  const summary = articles.length ? news?.sentiment_summary : undefined;

  let list;
  if (isLoading) {
    list = <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}><CircularProgress size={28} /></Box>;
  } else if (!articles.length) {
    list = <Typography variant="body2" color="text.secondary">No recent news available.</Typography>;
  } else {
    list = <ArticleList articles={articles} />;
  }

  return (
    <Paper sx={{ p: 2 }}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
        <SectionHeader title="Recent News & Sentiment" />
        <Button size="small" color="inherit" startIcon={<RefreshIcon />} onClick={() => refetch()} sx={{ fontSize: 11 }}>
          Refresh
        </Button>
      </Stack>
      {summary?.overall && <OverallRow summary={summary} />}
      <Box sx={{ mb: 1.5 }}>
        <SentimentTrendChart ticker={ticker} />
      </Box>
      {list}
    </Paper>
  );
}
