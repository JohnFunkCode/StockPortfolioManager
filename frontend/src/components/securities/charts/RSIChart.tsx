/**
 * RSIChart — D3 RSI oscillator with overbought/oversold bands.
 */
import { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { Box, Typography } from '@mui/material';
import type { TechnicalIndicator } from '../../../api/securitiesTypes';

interface Props {
  data: TechnicalIndicator[];
  height?: number;
}

const MARGIN = { top: 12, right: 16, bottom: 28, left: 40 };

export default function RSIChart({ data, height = 140 }: Props) {
  const ref = useRef<SVGSVGElement>(null);

  useEffect(() => {
    if (!ref.current || data.length === 0) return;
    const svg = d3.select(ref.current);
    svg.selectAll('*').remove();

    const width = ref.current.parentElement!.clientWidth;
    const W = width - MARGIN.left - MARGIN.right;
    const H = height - MARGIN.top - MARGIN.bottom;

    const valid = data.filter((d) => d.rsi != null);
    if (valid.length === 0) return;

    const xScale = d3
      .scaleTime()
      .domain(d3.extent(valid, (d) => new Date(d.date)) as [Date, Date])
      .range([0, W]);
    const yScale = d3.scaleLinear().domain([0, 100]).range([H, 0]);

    const g = svg
      .attr('width', width)
      .attr('height', height)
      .append('g')
      .attr('transform', `translate(${MARGIN.left},${MARGIN.top})`);

    // Overbought / oversold bands
    g.append('rect')
      .attr('x', 0).attr('y', yScale(100))
      .attr('width', W).attr('height', yScale(70) - yScale(100))
      .attr('fill', '#ef4444').attr('fill-opacity', 0.08);

    g.append('rect')
      .attr('x', 0).attr('y', yScale(30))
      .attr('width', W).attr('height', yScale(0) - yScale(30))
      .attr('fill', '#10b981').attr('fill-opacity', 0.08);

    // Reference lines at 30 / 70
    [30, 50, 70].forEach((lvl) => {
      g.append('line')
        .attr('x1', 0).attr('x2', W)
        .attr('y1', yScale(lvl)).attr('y2', yScale(lvl))
        .attr('stroke', lvl === 50 ? '#6b7280' : lvl === 70 ? '#ef4444' : '#10b981')
        .attr('stroke-width', 1)
        .attr('stroke-dasharray', '3,3')
        .attr('opacity', 0.6);

      g.append('text')
        .attr('x', -4).attr('y', yScale(lvl))
        .attr('dy', '0.35em')
        .attr('text-anchor', 'end')
        .attr('fill', '#6b7280')
        .attr('font-size', 9)
        .text(lvl);
    });

    // RSI line coloured by zone
    const rsiLine = d3
      .line<TechnicalIndicator>()
      .defined((d) => d.rsi != null)
      .x((d) => xScale(new Date(d.date)))
      .y((d) => yScale(d.rsi!));

    g.append('path')
      .datum(valid)
      .attr('d', rsiLine)
      .attr('fill', 'none')
      .attr('stroke', '#a855f7')
      .attr('stroke-width', 1.5);

    // X axis
    g.append('g')
      .attr('transform', `translate(0,${H})`)
      .call(d3.axisBottom(xScale).ticks(5).tickFormat(d3.timeFormat('%b %d') as never))
      .selectAll('text')
      .attr('fill', '#9ca3af')
      .attr('font-size', 10);

    g.selectAll('.domain').attr('stroke', '#374151');
    g.selectAll('.tick line').attr('stroke', '#374151');

    // Tooltip
    const tooltip = d3.select('body').select<HTMLDivElement>('#price-tooltip');
    const bisect = d3.bisector<TechnicalIndicator, Date>((d) => new Date(d.date)).left;
    const dot = g.append('circle').attr('r', 4).attr('fill', '#a855f7').style('display', 'none');

    g.append('rect')
      .attr('width', W).attr('height', H)
      .attr('fill', 'none')
      .attr('pointer-events', 'all')
      .on('mousemove', function (event) {
        const [mx] = d3.pointer(event);
        const x0 = xScale.invert(mx);
        const i = bisect(valid, x0, 1);
        const d = valid[Math.min(i, valid.length - 1)];
        dot.style('display', null)
          .attr('cx', xScale(new Date(d.date)))
          .attr('cy', yScale(d.rsi!));
        tooltip
          .style('display', 'block')
          .style('left', `${event.pageX + 12}px`)
          .style('top', `${event.pageY - 28}px`)
          .html(`<strong>${d.date}</strong><br/>RSI: <strong>${d.rsi?.toFixed(1)}</strong>`);
      })
      .on('mouseleave', () => {
        dot.style('display', 'none');
        tooltip.style('display', 'none');
      });
  }, [data, height]);

  const latest = data.filter((d) => d.rsi != null).at(-1);

  return (
    <Box>
      <Typography variant="caption" sx={{ px: 1, color: '#a855f7' }}>
        RSI (14){latest?.rsi != null && ` — ${latest.rsi.toFixed(1)}`}
        {latest?.rsi != null && latest.rsi >= 70 && ' ⚠ Overbought'}
        {latest?.rsi != null && latest.rsi <= 30 && ' ⚠ Oversold'}
      </Typography>
      <svg ref={ref} style={{ width: '100%', display: 'block' }} />
    </Box>
  );
}
