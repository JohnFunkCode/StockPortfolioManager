/**
 * VolumeChart — D3 bar chart of daily trading volume.
 */
import { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { Box, Typography } from '@mui/material';
import type { TechnicalIndicator } from '../../../api/securitiesTypes';

interface Props {
  data: TechnicalIndicator[];
  height?: number;
}

const MARGIN = { top: 8, right: 16, bottom: 28, left: 60 };

function formatVolume(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(0)}K`;
  return `${v}`;
}

export default function VolumeChart({ data, height = 100 }: Props) {
  const ref = useRef<SVGSVGElement>(null);

  useEffect(() => {
    if (!ref.current || data.length === 0) return;
    const svg = d3.select(ref.current);
    svg.selectAll('*').remove();

    const width = ref.current.parentElement!.clientWidth;
    const W = width - MARGIN.left - MARGIN.right;
    const H = height - MARGIN.top - MARGIN.bottom;

    const xScale = d3
      .scaleTime()
      .domain(d3.extent(data, (d) => new Date(d.date)) as [Date, Date])
      .range([0, W]);

    const yScale = d3
      .scaleLinear()
      .domain([0, d3.max(data, (d) => d.volume) as number])
      .nice()
      .range([H, 0]);

    const g = svg
      .attr('width', width)
      .attr('height', height)
      .append('g')
      .attr('transform', `translate(${MARGIN.left},${MARGIN.top})`);

    const barWidth = Math.max(1, W / data.length - 0.5);

    // Colour bars by comparing close vs previous close
    g.selectAll('.vol-bar')
      .data(data)
      .join('rect')
      .attr('x', (d) => xScale(new Date(d.date)) - barWidth / 2)
      .attr('y', (d) => yScale(d.volume))
      .attr('width', barWidth)
      .attr('height', (d) => H - yScale(d.volume))
      .attr('fill', (d, i) => {
        const prev = data[i - 1];
        if (!prev || d.close == null || prev.close == null) return '#6b7280';
        return d.close >= prev.close ? '#10b981' : '#ef4444';
      })
      .attr('opacity', 0.7);

    // 20-day avg volume line
    const avgVol = d3.mean(data, (d) => d.volume) ?? 0;
    g.append('line')
      .attr('x1', 0).attr('x2', W)
      .attr('y1', yScale(avgVol)).attr('y2', yScale(avgVol))
      .attr('stroke', '#f59e0b')
      .attr('stroke-dasharray', '3,3')
      .attr('opacity', 0.8);

    // Axes
    g.append('g')
      .attr('transform', `translate(0,${H})`)
      .call(d3.axisBottom(xScale).ticks(5).tickFormat(d3.timeFormat('%b %d') as never))
      .selectAll('text')
      .attr('fill', '#9ca3af')
      .attr('font-size', 10);

    g.append('g')
      .call(d3.axisLeft(yScale).ticks(3).tickFormat((v) => formatVolume(v as number)))
      .selectAll('text')
      .attr('fill', '#9ca3af')
      .attr('font-size', 10);

    g.selectAll('.domain').attr('stroke', '#374151');
    g.selectAll('.tick line').attr('stroke', '#374151');

    // Tooltip
    const tooltip = d3.select('body').select<HTMLDivElement>('#price-tooltip');
    const bisect = d3.bisector<TechnicalIndicator, Date>((d) => new Date(d.date)).left;

    g.append('rect')
      .attr('width', W).attr('height', H)
      .attr('fill', 'none')
      .attr('pointer-events', 'all')
      .on('mousemove', function (event) {
        const [mx] = d3.pointer(event);
        const x0 = xScale.invert(mx);
        const i = bisect(data, x0, 1);
        const d = data[Math.min(i, data.length - 1)];
        tooltip
          .style('display', 'block')
          .style('left', `${event.pageX + 12}px`)
          .style('top', `${event.pageY - 28}px`)
          .html(`<strong>${d.date}</strong><br/>Volume: <strong>${formatVolume(d.volume)}</strong>`);
      })
      .on('mouseleave', () => tooltip.style('display', 'none'));
  }, [data, height]);

  return (
    <Box>
      <Typography variant="caption" sx={{ px: 1, color: '#9ca3af' }}>
        Volume
        <span style={{ color: '#f59e0b', marginLeft: 8 }}>╌ Avg</span>
      </Typography>
      <svg ref={ref} style={{ width: '100%', display: 'block' }} />
    </Box>
  );
}
