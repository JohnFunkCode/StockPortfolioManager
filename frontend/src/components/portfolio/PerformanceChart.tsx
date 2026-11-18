/**
 * PerformanceChart — the growth of $1 in one or more books against SPY
 * (#431 step 5.3).
 *
 * Every value arrives computed: each line is a series of `index` points the
 * server chained from the book's daily snapshots (time-weighted, so a deposit
 * is not a rise), and SPY's line is its own `benchmark_index` over the same
 * sessions (Rule 8.4). This file scales and draws; the y axis is the index as
 * given, labelled as dollars because that is what the growth of $1 is.
 *
 * A session with no value is simply not a point: the line joins the sessions
 * either side of it, and the section above says how many there were.
 */
import { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { Box, Stack, Typography, useTheme } from '@mui/material';

export interface ChartLine {
  key: string;
  label: string;
  points: Array<{ date: string; value: number | null }>;
  /** The benchmark is drawn dashed and muted, under the books. */
  benchmark?: boolean;
}

interface Props {
  lines: ChartLine[];
  height?: number;
}

const MARGIN = { top: 12, right: 16, bottom: 26, left: 52 };

/** A session date as a local calendar day, not UTC midnight (see formatDate). */
function day(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d);
}

type Point = { date: Date; value: number };

function drawable(line: ChartLine): Point[] {
  return line.points
    .filter((p): p is { date: string; value: number } => p.value != null)
    .map((p) => ({ date: day(p.date), value: p.value }));
}

export default function PerformanceChart({ lines, height = 260 }: Props) {
  const ref = useRef<SVGSVGElement>(null);
  const theme = useTheme();
  const palette = [
    theme.palette.primary.main,
    theme.palette.secondary.main,
    theme.palette.success.main,
    theme.palette.warning.main,
    theme.palette.info.main,
    theme.palette.error.main,
  ];
  const muted = theme.palette.text.secondary;
  const books = lines.filter((line) => !line.benchmark);
  const colourOf = (line: ChartLine) =>
    line.benchmark ? muted : palette[books.indexOf(line) % palette.length];
  const drawn = lines.map((line) => ({ line, points: drawable(line) }));
  const hasData = drawn.some(({ points }) => points.length > 0);

  useEffect(() => {
    if (!ref.current || !hasData) return;
    const svg = d3.select(ref.current);
    svg.selectAll('*').remove();

    const width = ref.current.parentElement?.clientWidth || 640;
    const W = Math.max(120, width - MARGIN.left - MARGIN.right);
    const H = height - MARGIN.top - MARGIN.bottom;
    const all = drawn.flatMap(({ points }) => points);

    const xScale = d3
      .scaleTime()
      .domain(d3.extent(all, (p) => p.date) as [Date, Date])
      .range([0, W]);
    const lo = d3.min(all, (p) => p.value) ?? 1;
    const hi = d3.max(all, (p) => p.value) ?? 1;
    const pad = (hi - lo) * 0.08 || 0.01;
    const yScale = d3.scaleLinear().domain([lo - pad, hi + pad]).range([H, 0]);

    const g = svg
      .attr('width', width)
      .attr('height', height)
      .append('g')
      .attr('transform', `translate(${MARGIN.left},${MARGIN.top})`);

    // Where every line started: $1.
    if (lo - pad <= 1 && hi + pad >= 1) {
      g.append('line')
        .attr('class', 'start-line')
        .attr('x1', 0)
        .attr('x2', W)
        .attr('y1', yScale(1))
        .attr('y2', yScale(1))
        .style('stroke', muted)
        .attr('stroke-dasharray', '2,3')
        .attr('opacity', 0.6);
    }

    // The benchmark first, so the books are drawn over it.
    const ordered = [...drawn].sort((a, b) => Number(!a.line.benchmark) - Number(!b.line.benchmark));
    for (const { line, points } of ordered) {
      if (points.length === 0) continue;
      g.append('path')
        .datum(points)
        .attr('class', line.benchmark ? 'benchmark-line' : 'book-line')
        .attr('data-key', line.key)
        .attr('fill', 'none')
        .style('stroke', colourOf(line))
        .attr('stroke-width', line.benchmark ? 1.25 : 1.75)
        .attr('stroke-dasharray', line.benchmark ? '5,4' : null)
        .attr(
          'd',
          d3
            .line<Point>()
            .x((p) => xScale(p.date))
            .y((p) => yScale(p.value)),
        );
    }

    g.append('g')
      .attr('transform', `translate(0,${H})`)
      .call(d3.axisBottom(xScale).ticks(5).tickFormat(d3.timeFormat('%b %d') as never))
      .attr('color', muted)
      .attr('font-size', 9);

    g.append('g')
      .call(d3.axisLeft(yScale).ticks(4).tickFormat(d3.format('$.2f') as never))
      .attr('color', muted)
      .attr('font-size', 9);
    // `drawn` and `colourOf` are derived from `lines` and `theme` each render.
  }, [lines, height, theme, hasData]);

  if (!hasData) {
    return (
      <Typography variant="body2" color="text.secondary">
        No valued sessions to chart yet.
      </Typography>
    );
  }

  return (
    <Box data-testid="performance-chart">
      <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap sx={{ mb: 0.5 }}>
        {lines.map((line) => (
          <Stack key={line.key} direction="row" spacing={0.75} alignItems="center">
            <Box
              sx={{
                width: 14,
                height: 0,
                borderTop: `2px ${line.benchmark ? 'dashed' : 'solid'}`,
                borderColor: colourOf(line),
              }}
            />
            <Typography variant="caption" color="text.secondary">{line.label}</Typography>
          </Stack>
        ))}
      </Stack>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
        Growth of $1
      </Typography>
      <svg ref={ref} role="img" aria-label="Growth of $1 against SPY" />
    </Box>
  );
}
