/**
 * PremiumChart — a NAV vehicle's discount to net asset value over time.
 *
 * The approximation label is not decoration. Only the current capital
 * structure is known, so every point applies today's holdings and senior
 * claims to that date's prices. A reader who takes this for a record of the
 * historical discount would draw exactly the wrong conclusion about how the
 * gap has behaved, so the caveat renders with the chart, always.
 */
import { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { Alert, Box, Stack, Typography, useTheme } from '@mui/material';
import type { PremiumHistoryResponse } from '../../api/arbitrage';

interface Props {
  data: PremiumHistoryResponse;
  height?: number;
}

const MARGIN = { top: 14, right: 20, bottom: 26, left: 46 };

export default function PremiumChart({ data, height = 220 }: Props) {
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

    const points = data.points.map((p) => ({
      date: new Date(p.date),
      pct: p.premium_discount_pct,
    }));

    const xScale = d3
      .scaleTime()
      .domain(d3.extent(points, (p) => p.date) as [Date, Date])
      .range([0, W]);

    // Always include zero: premium vs discount is the meaningful boundary.
    const lo = Math.min(0, d3.min(points, (p) => p.pct) ?? 0);
    const hi = Math.max(0, d3.max(points, (p) => p.pct) ?? 0);
    const pad = (hi - lo) * 0.1 || 1;
    const yScale = d3.scaleLinear().domain([lo - pad, hi + pad]).range([H, 0]);

    const g = svg
      .attr('width', width)
      .attr('height', height)
      .append('g')
      .attr('transform', `translate(${MARGIN.left},${MARGIN.top})`);

    // Parity line — above it the vehicle trades at a premium.
    g.append('line')
      .attr('x1', 0)
      .attr('x2', W)
      .attr('y1', yScale(0))
      .attr('y2', yScale(0))
      .attr('stroke', muted)
      .attr('stroke-width', 1)
      .attr('stroke-dasharray', '4,3')
      .attr('opacity', 0.8);

    g.append('path')
      .datum(points)
      .attr('fill', 'none')
      .attr('stroke', theme.palette.primary.main)
      .attr('stroke-width', 1.5)
      .attr(
        'd',
        d3
          .line<{ date: Date; pct: number }>()
          .x((p) => xScale(p.date))
          .y((p) => yScale(p.pct))
          .curve(d3.curveMonotoneX),
      );

    const last = points[points.length - 1];
    g.append('circle')
      .attr('cx', xScale(last.date))
      .attr('cy', yScale(last.pct))
      .attr('r', 4)
      .attr('fill', theme.palette.secondary.main);

    g.append('g')
      .attr('transform', `translate(0,${H})`)
      .call(d3.axisBottom(xScale).ticks(5).tickFormat(d3.timeFormat('%b %d') as never))
      .attr('color', muted)
      .attr('font-size', 9);

    g.append('g')
      .call(d3.axisLeft(yScale).ticks(4).tickFormat((v) => `${v}%`))
      .attr('color', muted)
      .attr('font-size', 9);
  }, [data, height, theme]);

  if (data.points.length === 0) {
    return (
      <Typography variant="body2" color="text.secondary">
        No premium history for {data.security}.
      </Typography>
    );
  }

  return (
    <Box>
      <Stack direction="row" spacing={2} alignItems="baseline" sx={{ mb: 0.5 }}>
        <Typography variant="subtitle2">
          {data.security} discount to NAV
        </Typography>
        <Typography variant="caption" color="text.secondary">
          latest{' '}
          {data.latest ? `${data.latest.premium_discount_pct.toFixed(2)}%` : '—'}
        </Typography>
      </Stack>
      <svg ref={ref} data-testid="premium-chart" />
      <Alert severity="info" icon={false} sx={{ mt: 1, py: 0, fontSize: 12 }}>
        {data.note}
      </Alert>
    </Box>
  );
}
