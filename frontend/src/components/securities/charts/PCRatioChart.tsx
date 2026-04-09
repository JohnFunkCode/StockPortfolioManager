/**
 * PCRatioChart — D3 put/call ratio over time with price overlay.
 */
import { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { Box, Typography } from '@mui/material';
import type { OptionsPCHistory } from '../../../api/securitiesTypes';

interface Props {
  data: OptionsPCHistory[];
  height?: number;
}

const MARGIN = { top: 12, right: 50, bottom: 32, left: 50 };

export default function PCRatioChart({ data, height = 200 }: Props) {
  const ref = useRef<SVGSVGElement>(null);

  useEffect(() => {
    if (!ref.current || data.length === 0) return;
    const svg = d3.select(ref.current);
    svg.selectAll('*').remove();

    const width = ref.current.parentElement!.clientWidth;
    const W = width - MARGIN.left - MARGIN.right;
    const H = height - MARGIN.top - MARGIN.bottom;

    const valid = data.filter((d) => d.put_call_ratio != null);
    if (valid.length === 0) return;

    const dates = valid.map((d) => new Date(d.captured_at));
    const xScale = d3.scaleTime().domain(d3.extent(dates) as [Date, Date]).range([0, W]);

    const pcVals = valid.map((d) => d.put_call_ratio!);
    const [pcMin, pcMax] = d3.extent(pcVals) as [number, number];
    const yLeft = d3.scaleLinear().domain([Math.min(pcMin, 0), pcMax * 1.1]).range([H, 0]);

    const prices = valid.map((d) => d.price);
    const [pMin, pMax] = d3.extent(prices) as [number, number];
    const pad = (pMax - pMin) * 0.05;
    const yRight = d3.scaleLinear().domain([pMin - pad, pMax + pad]).range([H, 0]);

    const g = svg
      .attr('width', width)
      .attr('height', height)
      .append('g')
      .attr('transform', `translate(${MARGIN.left},${MARGIN.top})`);

    // Grid
    g.append('g')
      .call(d3.axisLeft(yLeft).tickSize(-W).tickFormat(() => ''))
      .selectAll('line')
      .attr('stroke', '#374151')
      .attr('stroke-dasharray', '2,4');
    g.select('.domain').remove();

    // Neutral P/C = 1.0 reference
    g.append('line')
      .attr('x1', 0).attr('x2', W)
      .attr('y1', yLeft(1)).attr('y2', yLeft(1))
      .attr('stroke', '#6b7280')
      .attr('stroke-dasharray', '4,4');
    g.append('text')
      .attr('x', W + 4).attr('y', yLeft(1))
      .attr('dy', '0.35em')
      .attr('fill', '#6b7280')
      .attr('font-size', 9)
      .text('1.0');

    // Price line (right axis)
    const priceLine = d3
      .line<OptionsPCHistory>()
      .x((d) => xScale(new Date(d.captured_at)))
      .y((d) => yRight(d.price));

    g.append('path')
      .datum(valid)
      .attr('d', priceLine)
      .attr('fill', 'none')
      .attr('stroke', '#10b981')
      .attr('stroke-width', 1.5)
      .attr('opacity', 0.6);

    // P/C ratio area + line
    const pcArea = d3
      .area<OptionsPCHistory>()
      .defined((d) => d.put_call_ratio != null)
      .x((d) => xScale(new Date(d.captured_at)))
      .y0(yLeft(0))
      .y1((d) => yLeft(d.put_call_ratio!));

    g.append('path')
      .datum(valid)
      .attr('d', pcArea)
      .attr('fill', '#a855f7')
      .attr('fill-opacity', 0.15);

    const pcLine = d3
      .line<OptionsPCHistory>()
      .defined((d) => d.put_call_ratio != null)
      .x((d) => xScale(new Date(d.captured_at)))
      .y((d) => yLeft(d.put_call_ratio!));

    g.append('path')
      .datum(valid)
      .attr('d', pcLine)
      .attr('fill', 'none')
      .attr('stroke', '#a855f7')
      .attr('stroke-width', 2);

    // Axes
    g.append('g')
      .attr('transform', `translate(0,${H})`)
      .call(d3.axisBottom(xScale).ticks(6).tickFormat(d3.timeFormat('%b %d') as never))
      .selectAll('text')
      .attr('fill', '#9ca3af')
      .attr('font-size', 10);

    g.append('g')
      .call(d3.axisLeft(yLeft).ticks(5).tickFormat((v) => `${(v as number).toFixed(1)}`))
      .selectAll('text')
      .attr('fill', '#a855f7')
      .attr('font-size', 10);

    g.append('g')
      .attr('transform', `translate(${W},0)`)
      .call(d3.axisRight(yRight).ticks(5).tickFormat((v) => `$${v}`))
      .selectAll('text')
      .attr('fill', '#10b981')
      .attr('font-size', 10);

    g.selectAll('.domain').attr('stroke', '#374151');
    g.selectAll('.tick line').attr('stroke', '#374151');

    // Tooltip
    const tooltip = d3.select('body').select<HTMLDivElement>('#price-tooltip');
    const bisect = d3.bisector<OptionsPCHistory, Date>((d) => new Date(d.captured_at)).left;

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
            `<strong>${d.captured_at.slice(0, 10)}</strong><br/>` +
            `P/C: <strong>${d.put_call_ratio?.toFixed(2) ?? '—'}</strong><br/>` +
            `Price: $${d.price.toFixed(2)}`,
          );
      })
      .on('mouseleave', () => tooltip.style('display', 'none'));
  }, [data, height]);

  const latest = data.filter((d) => d.put_call_ratio != null).at(-1);

  return (
    <Box>
      <Typography variant="caption" sx={{ px: 1 }}>
        <span style={{ color: '#a855f7' }}>━ Put/Call Ratio</span>
        {latest?.put_call_ratio != null && (
          <strong style={{ marginLeft: 8 }}>{latest.put_call_ratio.toFixed(2)}</strong>
        )}
        <span style={{ color: '#10b981', marginLeft: 12 }}>━ Price</span>
      </Typography>
      <svg ref={ref} style={{ width: '100%', display: 'block' }} />
    </Box>
  );
}
