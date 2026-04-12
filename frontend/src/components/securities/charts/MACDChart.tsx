/**
 * MACDChart — D3 MACD histogram + MACD line + signal line.
 */
import { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { Box, Typography } from '@mui/material';
import type { TechnicalIndicator } from '../../../api/securitiesTypes';

interface Props {
  data: TechnicalIndicator[];
  height?: number;
}

const MARGIN = { top: 12, right: 16, bottom: 28, left: 50 };

export default function MACDChart({ data, height = 150 }: Props) {
  const ref = useRef<SVGSVGElement>(null);

  useEffect(() => {
    if (!ref.current || data.length === 0) return;
    const svg = d3.select(ref.current);
    svg.selectAll('*').remove();

    const width = ref.current.parentElement!.clientWidth;
    const W = width - MARGIN.left - MARGIN.right;
    const H = height - MARGIN.top - MARGIN.bottom;

    const valid = data.filter((d) => d.macd != null && d.macd_signal != null);
    if (valid.length === 0) return;

    const dates = valid.map((d) => new Date(d.date));
    const xScale = d3.scaleTime().domain(d3.extent(dates) as [Date, Date]).range([0, W]);

    const allVals = valid.flatMap((d) => [d.macd!, d.macd_signal!, d.macd_hist ?? 0]);
    const [yMin, yMax] = d3.extent(allVals) as [number, number];
    const pad = Math.max(Math.abs(yMax), Math.abs(yMin)) * 0.1;
    const yScale = d3.scaleLinear().domain([yMin - pad, yMax + pad]).range([H, 0]);

    const g = svg
      .attr('width', width)
      .attr('height', height)
      .append('g')
      .attr('transform', `translate(${MARGIN.left},${MARGIN.top})`);

    // Zero line
    g.append('line')
      .attr('x1', 0).attr('x2', W)
      .attr('y1', yScale(0)).attr('y2', yScale(0))
      .attr('stroke', '#4b5563')
      .attr('stroke-dasharray', '2,3');

    // Histogram bars
    const barWidth = Math.max(1, W / valid.length - 1);
    g.selectAll('.bar')
      .data(valid.filter((d) => d.macd_hist != null))
      .join('rect')
      .attr('x', (d) => xScale(new Date(d.date)) - barWidth / 2)
      .attr('y', (d) => d.macd_hist! >= 0 ? yScale(d.macd_hist!) : yScale(0))
      .attr('width', barWidth)
      .attr('height', (d) => Math.abs(yScale(d.macd_hist!) - yScale(0)))
      .attr('fill', (d) => (d.macd_hist! >= 0 ? '#10b981' : '#ef4444'))
      .attr('opacity', 0.6);

    // MACD line
    const macdLine = d3
      .line<TechnicalIndicator>()
      .defined((d) => d.macd != null)
      .x((d) => xScale(new Date(d.date)))
      .y((d) => yScale(d.macd!));

    g.append('path')
      .datum(valid)
      .attr('d', macdLine)
      .attr('fill', 'none')
      .attr('stroke', '#3b82f6')
      .attr('stroke-width', 1.5);

    // Signal line
    const signalLine = d3
      .line<TechnicalIndicator>()
      .defined((d) => d.macd_signal != null)
      .x((d) => xScale(new Date(d.date)))
      .y((d) => yScale(d.macd_signal!));

    g.append('path')
      .datum(valid)
      .attr('d', signalLine)
      .attr('fill', 'none')
      .attr('stroke', '#f59e0b')
      .attr('stroke-width', 1.5)
      .attr('stroke-dasharray', '4,2');

    // Axes
    g.append('g')
      .attr('transform', `translate(0,${H})`)
      .call(d3.axisBottom(xScale).ticks(5).tickFormat(d3.timeFormat('%b %d') as never))
      .selectAll('text')
      .attr('fill', '#9ca3af')
      .attr('font-size', 10);

    g.append('g')
      .call(d3.axisLeft(yScale).ticks(4).tickFormat((v) => `${v}`))
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
        const i = bisect(valid, x0, 1);
        const d = valid[Math.min(i, valid.length - 1)];
        tooltip
          .style('display', 'block')
          .style('left', `${event.pageX + 12}px`)
          .style('top', `${event.pageY - 28}px`)
          .html(
            `<strong>${d.date}</strong><br/>` +
            `MACD: ${d.macd?.toFixed(3) ?? '—'}<br/>` +
            `Signal: ${d.macd_signal?.toFixed(3) ?? '—'}<br/>` +
            `Hist: <span style="color:${(d.macd_hist ?? 0) >= 0 ? '#10b981' : '#ef4444'}">${d.macd_hist?.toFixed(3) ?? '—'}</span>`,
          );
      })
      .on('mouseleave', () => tooltip.style('display', 'none'));
  }, [data, height]);

  return (
    <Box>
      <Typography variant="caption" sx={{ px: 1, color: '#9ca3af' }}>
        MACD (12·26·9)
        <span style={{ color: '#3b82f6', marginLeft: 8 }}>━ MACD</span>
        <span style={{ color: '#f59e0b', marginLeft: 8 }}>╌ Signal</span>
        <span style={{ color: '#10b981', marginLeft: 8 }}>▮ Hist+</span>
        <span style={{ color: '#ef4444', marginLeft: 4 }}>▮ Hist−</span>
      </Typography>
      <svg ref={ref} style={{ width: '100%', display: 'block' }} />
    </Box>
  );
}
