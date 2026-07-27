/**
 * SpreadChart — the residual spread over time with its mean, ±1σ/±2σ bands,
 * fitted trend line, and the latest point called out.
 *
 * This is the object every arbitrage number describes and the one thing that
 * was never visible: `analyze_pair` computes the series and discards it. A
 * z-score of −1.35 tells you the gap is stretched; this shows whether it has
 * been oscillating around the mean or walking away from it.
 *
 * All levels arrive precomputed from the service (arch-v2 Rule 8) — this file
 * scales and draws, it never derives a statistic.
 */
import { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { Box, Stack, Typography, useTheme } from '@mui/material';
import type { SpreadHistoryResponse } from '../../api/arbitrage';

interface Props {
  data: SpreadHistoryResponse;
  height?: number;
}

const MARGIN = { top: 14, right: 58, bottom: 26, left: 52 };

export default function SpreadChart({ data, height = 240 }: Props) {
  const ref = useRef<SVGSVGElement>(null);
  const theme = useTheme();

  useEffect(() => {
    if (!ref.current || data.points.length === 0) return;
    const svg = d3.select(ref.current);
    svg.selectAll('*').remove();

    const width = ref.current.parentElement?.clientWidth || 640;
    const W = Math.max(120, width - MARGIN.left - MARGIN.right);
    const H = height - MARGIN.top - MARGIN.bottom;
    const muted = theme.palette.text.secondary;

    const points = data.points.map((p) => ({ date: new Date(p.date), spread: p.spread }));
    const { bands, mean } = data;

    const xScale = d3
      .scaleTime()
      .domain(d3.extent(points, (p) => p.date) as [Date, Date])
      .range([0, W]);

    // Domain spans the data AND the outer bands so a 2σ line is never clipped.
    const lo = Math.min(d3.min(points, (p) => p.spread) ?? 0, bands.minus_two);
    const hi = Math.max(d3.max(points, (p) => p.spread) ?? 0, bands.plus_two);
    const pad = (hi - lo) * 0.08 || 0.01;
    const yScale = d3.scaleLinear().domain([lo - pad, hi + pad]).range([H, 0]);

    const g = svg
      .attr('width', width)
      .attr('height', height)
      .append('g')
      .attr('transform', `translate(${MARGIN.left},${MARGIN.top})`);

    // ±1σ shaded channel, then the ±2σ outer lines.
    g.append('rect')
      .attr('x', 0)
      .attr('y', yScale(bands.plus_one))
      .attr('width', W)
      .attr('height', Math.max(0, yScale(bands.minus_one) - yScale(bands.plus_one)))
      .attr('fill', theme.palette.primary.main)
      .attr('fill-opacity', 0.07);

    const level = (value: number, label: string, dash: string, colour: string) => {
      g.append('line')
        .attr('class', 'level-line')
        .attr('x1', 0)
        .attr('x2', W)
        .attr('y1', yScale(value))
        .attr('y2', yScale(value))
        .attr('stroke', colour)
        .attr('stroke-width', 1)
        .attr('stroke-dasharray', dash)
        .attr('opacity', 0.7);
      g.append('text')
        .attr('x', W + 4)
        .attr('y', yScale(value))
        .attr('dy', '0.35em')
        .attr('fill', muted)
        .attr('font-size', 9)
        .text(label);
    };

    level(bands.plus_two, '+2σ', '2,3', theme.palette.warning.main);
    level(bands.minus_two, '−2σ', '2,3', theme.palette.warning.main);
    level(mean, 'mean', '4,3', muted);

    // Fitted trend: y = intercept + slope * barIndex (both from the service).
    const { intercept, slope_per_day: slope } = data.trend;
    if (intercept != null && slope != null) {
      g.append('line')
        .attr('class', 'trend-line')
        .attr('x1', xScale(points[0].date))
        .attr('x2', xScale(points[points.length - 1].date))
        .attr('y1', yScale(intercept))
        .attr('y2', yScale(intercept + slope * (points.length - 1)))
        .attr('stroke', data.trend.widening ? theme.palette.error.main : muted)
        .attr('stroke-width', 1.5)
        .attr('stroke-dasharray', '6,4')
        .attr('opacity', 0.8);
    }

    g.append('path')
      .datum(points)
      .attr('fill', 'none')
      .attr('stroke', theme.palette.primary.main)
      .attr('stroke-width', 1.5)
      .attr(
        'd',
        d3
          .line<{ date: Date; spread: number }>()
          .x((p) => xScale(p.date))
          .y((p) => yScale(p.spread))
          .curve(d3.curveMonotoneX),
      );

    const last = points[points.length - 1];
    g.append('circle')
      .attr('cx', xScale(last.date))
      .attr('cy', yScale(last.spread))
      .attr('r', 4)
      .attr('fill', theme.palette.secondary.main);

    g.append('g')
      .attr('transform', `translate(0,${H})`)
      .call(d3.axisBottom(xScale).ticks(5).tickFormat(d3.timeFormat('%b %d') as never))
      .attr('color', muted)
      .attr('font-size', 9);

    g.append('g')
      .call(d3.axisLeft(yScale).ticks(4))
      .attr('color', muted)
      .attr('font-size', 9);
  }, [data, height, theme]);

  if (data.points.length === 0) {
    return (
      <Typography variant="body2" color="text.secondary">
        No spread history for {data.security}.
      </Typography>
    );
  }

  return (
    <Box>
      <Stack direction="row" spacing={2} alignItems="baseline" sx={{ mb: 0.5 }}>
        <Typography variant="subtitle2">
          {data.security} − {data.underlying} spread
        </Typography>
        <Typography variant="caption" color="text.secondary">
          latest {data.latest.z == null ? '—' : `${data.latest.z.toFixed(2)}σ`}
          {data.trend.widening ? ' · widening' : ''}
          {data.half_life.half_life_days != null
            ? ` · half-life ${data.half_life.half_life_days.toFixed(1)}d`
            : ''}
        </Typography>
      </Stack>
      <svg ref={ref} data-testid="spread-chart" />
    </Box>
  );
}
