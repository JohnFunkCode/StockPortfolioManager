/**
 * PortfolioAllocationChart — the legacy HTML report's left-hand stacked bar
 * (issue #147 Part A), rebuilt in d3 on the Portfolio page.
 *
 * One bar per symbol: an orange cost-basis block from zero, with the gain
 * stacked *above* it in green, or the loss cut *down* into it in red. Either
 * way the visible top of the bar is the position's current value, so bar
 * heights compare as allocation and the coloured slice reads as performance.
 *
 * Every number is taken straight off the row (bar_base / bar_gain / bar_loss,
 * computed by quantcore.analytics.portfolio_math.allocation_segments) — no
 * gain/loss math happens in this file (Rule 8.4). The only arithmetic here is
 * pixel scaling.
 */
import { useEffect, useMemo, useRef } from 'react';
import * as d3 from 'd3';
import { Box, Stack, Typography } from '@mui/material';
import { formatCurrency } from '../../utils/formatting';
import type { SymbolRow } from '../../api/portfolioTypes';

interface Props {
  rows: SymbolRow[];
  height?: number;
}

const MARGIN = { top: 16, right: 16, bottom: 56, left: 72 };

const COLORS = {
  base: '#f59e0b',
  gain: '#10b981',
  loss: '#ef4444',
};

/** Don't print a label that won't fit inside its own slice. */
const MIN_LABEL_PX = 14;

// The page's own currency formatter, so a bar's label reads identically to the
// same number in the holdings table below it (d3's own $ format uses a U+2212
// minus, which would not match).
const money = (v: number) => formatCurrency(v);

export default function PortfolioAllocationChart({ rows, height = 340 }: Props) {
  const ref = useRef<SVGSVGElement>(null);

  // A symbol with no priced lot arrives with null segments — it has no value to
  // draw, and a zero-height bar would read as a worthless position rather than
  // an unknown one, so it is dropped. Biggest position first: the point of the
  // chart is relative size, which a sorted axis makes legible at a glance.
  const drawable = useMemo(
    () =>
      rows
        .filter((r) => r.bar_base != null && r.bar_gain != null && r.bar_loss != null)
        .map((r) => ({
          symbol: r.symbol,
          base: r.bar_base as number,
          gain: r.bar_gain as number,
          loss: r.bar_loss as number,
        }))
        .sort((a, b) => b.base + b.gain + b.loss - (a.base + a.gain + a.loss)),
    [rows],
  );

  useEffect(() => {
    if (!ref.current || drawable.length === 0) return;
    const svg = d3.select(ref.current);
    svg.selectAll('*').remove();

    const width = ref.current.parentElement?.clientWidth || 720;
    const W = width - MARGIN.left - MARGIN.right;
    const H = height - MARGIN.top - MARGIN.bottom;

    const xScale = d3
      .scaleBand<string>()
      .domain(drawable.map((d) => d.symbol))
      .range([0, W])
      .padding(0.25);

    // Top of the tallest bar is a winner's base+gain; a loser's top is its base.
    const yMax = d3.max(drawable, (d) => d.base + d.gain) ?? 0;
    const yScale = d3
      .scaleLinear()
      .domain([0, yMax * 1.05 || 1])
      .range([H, 0]);

    const g = svg
      .attr('width', width)
      .attr('height', height)
      .append('g')
      .attr('transform', `translate(${MARGIN.left},${MARGIN.top})`);

    g.append('g')
      .attr('class', 'grid')
      .call(d3.axisLeft(yScale).tickSize(-W).tickFormat(() => ''))
      .selectAll('line')
      .attr('stroke', '#374151')
      .attr('stroke-dasharray', '2,4');
    g.select('.grid .domain').remove();

    const bandWidth = xScale.bandwidth();

    const label = (text: string, x: number, top: number, bottom: number) => {
      if (bottom - top < MIN_LABEL_PX) return;
      g.append('text')
        .attr('x', x)
        .attr('y', (top + bottom) / 2)
        .attr('text-anchor', 'middle')
        .attr('dominant-baseline', 'middle')
        .attr('fill', '#f9fafb')
        .attr('font-size', 10)
        .text(text);
    };

    drawable.forEach((d) => {
      const x = xScale(d.symbol) ?? 0;
      const mid = x + bandWidth / 2;

      // Cost basis, from zero.
      g.append('rect')
        .attr('data-testid', `alloc-base-${d.symbol}`)
        .attr('x', x)
        .attr('y', yScale(d.base))
        .attr('width', bandWidth)
        .attr('height', Math.max(0, yScale(0) - yScale(d.base)))
        .attr('fill', COLORS.base);
      label(money(d.base), mid, yScale(d.base), yScale(0));

      // Gain stacks above the cost basis.
      if (d.gain > 0) {
        g.append('rect')
          .attr('data-testid', `alloc-gain-${d.symbol}`)
          .attr('x', x)
          .attr('y', yScale(d.base + d.gain))
          .attr('width', bandWidth)
          .attr('height', Math.max(0, yScale(d.base) - yScale(d.base + d.gain)))
          .attr('fill', COLORS.gain);
        label(money(d.gain), mid, yScale(d.base + d.gain), yScale(d.base));
      }

      // Loss is negative: drawn down from the cost basis, overlaying its top —
      // exactly what the matplotlib bottom= bar with a negative height did.
      if (d.loss < 0) {
        g.append('rect')
          .attr('data-testid', `alloc-loss-${d.symbol}`)
          .attr('x', x)
          .attr('y', yScale(d.base))
          .attr('width', bandWidth)
          .attr('height', Math.max(0, yScale(d.base + d.loss) - yScale(d.base)))
          .attr('fill', COLORS.loss);
        label(money(d.loss), mid, yScale(d.base), yScale(d.base + d.loss));
      }
    });

    g.append('g')
      .attr('transform', `translate(0,${H})`)
      .call(d3.axisBottom(xScale))
      .selectAll('text')
      .attr('fill', '#9ca3af')
      .attr('transform', 'rotate(-40)')
      .attr('text-anchor', 'end')
      .attr('dx', '-0.4em')
      .attr('dy', '0.5em');

    g.append('g')
      .call(d3.axisLeft(yScale).ticks(5).tickFormat((v) => d3.format('$,')(v as number)))
      .selectAll('text')
      .attr('fill', '#9ca3af');

    g.selectAll('.domain').attr('stroke', '#374151');
    g.selectAll('.tick line').attr('stroke', '#374151');
  }, [drawable, height]);

  if (drawable.length === 0) return null;

  return (
    <Box data-testid="portfolio-allocation-chart">
      <Stack direction="row" spacing={3} sx={{ mb: 1, px: 1, flexWrap: 'wrap' }}>
        <Typography variant="body2" sx={{ color: COLORS.base }}>■ Cost Basis</Typography>
        <Typography variant="body2" sx={{ color: COLORS.gain }}>■ Gain</Typography>
        <Typography variant="body2" sx={{ color: COLORS.loss }}>■ Loss</Typography>
      </Stack>
      <svg ref={ref} style={{ width: '100%', display: 'block' }} />
    </Box>
  );
}
