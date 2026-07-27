/**
 * DiscoveryScatter — every tested pair from a cointegration sweep, plotted
 * against the critical value that decides it.
 *
 * Built for the near-misses. A pass/fail list said "0 found" for NEM/AEM/FCX;
 * the scatter shows NEM sitting at −2.87 against a −3.04 threshold with 0.73
 * correlation — one tick from qualifying. Points left of the line passed,
 * hollow points did not.
 */
import { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { Box, Stack, Typography, useTheme } from '@mui/material';
import type { TestedPair } from '../../api/arbitrage';

interface Props {
  tested: TestedPair[];
  /** Engle-Granger critical values by level, e.g. {"0.05": -3.34}. */
  criticalValues?: Record<string, number>;
  height?: number;
}

const MARGIN = { top: 16, right: 20, bottom: 34, left: 46 };

export default function DiscoveryScatter({ tested, criticalValues, height = 260 }: Props) {
  const ref = useRef<SVGSVGElement>(null);
  const theme = useTheme();

  const plottable = tested.filter((t) => t.statistic != null && t.correlation != null);

  useEffect(() => {
    if (!ref.current || plottable.length === 0) return;
    const svg = d3.select(ref.current);
    svg.selectAll('*').remove();

    const width = ref.current.parentElement?.clientWidth || 640;
    const W = Math.max(120, width - MARGIN.left - MARGIN.right);
    const H = height - MARGIN.top - MARGIN.bottom;
    const muted = theme.palette.text.secondary;

    const crits = Object.values(criticalValues ?? {});
    const stats = plottable.map((t) => t.statistic as number);
    const xLo = Math.min(...stats, ...crits) - 0.4;
    const xHi = Math.max(...stats, ...crits) + 0.4;

    const xScale = d3.scaleLinear().domain([xLo, xHi]).range([0, W]);
    const yScale = d3.scaleLinear().domain([0, 1]).range([H, 0]);

    const g = svg
      .attr('width', width)
      .attr('height', height)
      .append('g')
      .attr('transform', `translate(${MARGIN.left},${MARGIN.top})`);

    // Critical-value lines: everything left of one clears that level.
    Object.entries(criticalValues ?? {}).forEach(([level, value]) => {
      g.append('line')
        .attr('class', 'critical-line')
        .attr('x1', xScale(value))
        .attr('x2', xScale(value))
        .attr('y1', 0)
        .attr('y2', H)
        .attr('stroke', theme.palette.warning.main)
        .attr('stroke-width', 1)
        .attr('stroke-dasharray', '3,3')
        .attr('opacity', 0.7);
      g.append('text')
        .attr('x', xScale(value))
        .attr('y', -4)
        .attr('text-anchor', 'middle')
        .attr('fill', muted)
        .attr('font-size', 9)
        .text(`${(Number(level) * 100).toFixed(0)}%`);
    });

    g.selectAll('circle')
      .data(plottable)
      .enter()
      .append('circle')
      .attr('cx', (t) => xScale(t.statistic as number))
      .attr('cy', (t) => yScale(Math.abs(t.correlation as number)))
      .attr('r', 5)
      .attr('fill', (t) => (t.passed ? theme.palette.success.main : 'none'))
      .attr('stroke', (t) =>
        t.passed ? theme.palette.success.main : theme.palette.text.secondary,
      )
      .attr('stroke-width', 1.5)
      .append('title')
      .text(
        (t) =>
          `${t.security} ~ ${t.underlying}\n` +
          `statistic ${(t.statistic as number).toFixed(2)} · ` +
          `|corr| ${Math.abs(t.correlation as number).toFixed(2)}` +
          (t.half_life_days != null ? ` · half-life ${t.half_life_days.toFixed(1)}d` : '') +
          (t.passed ? '' : `\nrejected: ${t.failed_because}`),
      );

    g.append('g')
      .attr('transform', `translate(0,${H})`)
      .call(d3.axisBottom(xScale).ticks(5))
      .attr('color', muted)
      .attr('font-size', 9);

    g.append('g')
      .call(d3.axisLeft(yScale).ticks(4))
      .attr('color', muted)
      .attr('font-size', 9);

    g.append('text')
      .attr('x', W / 2)
      .attr('y', H + 28)
      .attr('text-anchor', 'middle')
      .attr('fill', muted)
      .attr('font-size', 9)
      .text('Engle-Granger statistic (more negative = stronger)');
  }, [plottable, criticalValues, height, theme]);

  if (plottable.length === 0) {
    return (
      <Typography variant="body2" color="text.secondary">
        Nothing testable in this sweep — the symbols had no usable price history.
      </Typography>
    );
  }

  const passed = plottable.filter((t) => t.passed).length;

  return (
    <Box>
      <Stack direction="row" spacing={2} alignItems="baseline" sx={{ mb: 0.5 }}>
        <Typography variant="subtitle2">Cointegration sweep</Typography>
        <Typography variant="caption" color="text.secondary">
          {passed} of {plottable.length} pairs passed · filled = passed, hollow = rejected
        </Typography>
      </Stack>
      <svg ref={ref} data-testid="discovery-scatter" />
    </Box>
  );
}
