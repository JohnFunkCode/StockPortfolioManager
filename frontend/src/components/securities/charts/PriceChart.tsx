/**
 * PriceChart — D3 line chart with close price, configurable moving averages,
 * and Bollinger Bands shading.
 */
import { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { Box, Stack, Typography } from '@mui/material';
import type { TechnicalIndicator } from '../../../api/securitiesTypes';

interface Props {
  data: TechnicalIndicator[];
  showMAs?: { ma50?: boolean; ma200?: boolean; ma30?: boolean };
  showBB?: boolean;
  height?: number;
  earningsDates?: string[]; // YYYY-MM-DD strings to mark on chart
}

const MARGIN = { top: 16, right: 16, bottom: 32, left: 60 };

const MA_COLORS = {
  ma30:  '#f59e0b',
  ma50:  '#3b82f6',
  ma200: '#ef4444',
};

export default function PriceChart({
  data,
  showMAs = { ma50: true, ma200: true },
  showBB = true,
  height = 300,
  earningsDates = [],
}: Props) {
  const ref = useRef<SVGSVGElement>(null);

  useEffect(() => {
    if (!ref.current || data.length === 0) return;
    const svg = d3.select(ref.current);
    svg.selectAll('*').remove();

    const width = ref.current.parentElement!.clientWidth;
    const W = width - MARGIN.left - MARGIN.right;
    const H = height - MARGIN.top - MARGIN.bottom;

    const valid = data.filter((d) => d.close != null);
    const dates = valid.map((d) => new Date(d.date));

    const xScale = d3.scaleTime().domain(d3.extent(dates) as [Date, Date]).range([0, W]);
    const allY: number[] = valid.flatMap((d) => {
      const vals: number[] = [];
      if (d.close != null) vals.push(d.close);
      if (showBB && d.bb_upper != null) vals.push(d.bb_upper);
      if (showBB && d.bb_lower != null) vals.push(d.bb_lower);
      return vals;
    });
    const [yMin, yMax] = d3.extent(allY) as [number, number];
    const pad = (yMax - yMin) * 0.05;
    const yScale = d3.scaleLinear().domain([yMin - pad, yMax + pad]).range([H, 0]);

    const g = svg
      .attr('width', width)
      .attr('height', height)
      .append('g')
      .attr('transform', `translate(${MARGIN.left},${MARGIN.top})`);

    // Grid lines
    g.append('g')
      .attr('class', 'grid')
      .call(
        d3.axisLeft(yScale).tickSize(-W).tickFormat(() => ''),
      )
      .selectAll('line')
      .attr('stroke', '#374151')
      .attr('stroke-dasharray', '2,4');
    g.select('.grid .domain').remove();

    // Bollinger Bands shading
    if (showBB) {
      const bbArea = d3
        .area<TechnicalIndicator>()
        .defined((d) => d.bb_upper != null && d.bb_lower != null)
        .x((d) => xScale(new Date(d.date)))
        .y0((d) => yScale(d.bb_lower!))
        .y1((d) => yScale(d.bb_upper!));

      g.append('path')
        .datum(valid)
        .attr('d', bbArea)
        .attr('fill', '#6366f1')
        .attr('fill-opacity', 0.1);

      const bbLine = (field: 'bb_upper' | 'bb_lower' | 'bb_middle') =>
        d3
          .line<TechnicalIndicator>()
          .defined((d) => d[field] != null)
          .x((d) => xScale(new Date(d.date)))
          .y((d) => yScale(d[field]!));

      (['bb_upper', 'bb_lower'] as const).forEach((f) => {
        g.append('path')
          .datum(valid)
          .attr('d', bbLine(f))
          .attr('fill', 'none')
          .attr('stroke', '#6366f1')
          .attr('stroke-width', 1)
          .attr('stroke-dasharray', '3,3')
          .attr('opacity', 0.6);
      });

      g.append('path')
        .datum(valid)
        .attr('d', bbLine('bb_middle'))
        .attr('fill', 'none')
        .attr('stroke', '#6366f1')
        .attr('stroke-width', 1)
        .attr('opacity', 0.4);
    }

    // Moving averages
    (Object.entries(showMAs) as [keyof typeof showMAs, boolean][])
      .filter(([, show]) => show)
      .forEach(([key]) => {
        const field = key as keyof TechnicalIndicator;
        const line = d3
          .line<TechnicalIndicator>()
          .defined((d) => d[field] != null)
          .x((d) => xScale(new Date(d.date)))
          .y((d) => yScale(d[field] as number));

        g.append('path')
          .datum(valid)
          .attr('d', line)
          .attr('fill', 'none')
          .attr('stroke', MA_COLORS[key as keyof typeof MA_COLORS] ?? '#9ca3af')
          .attr('stroke-width', 1.5)
          .attr('opacity', 0.85);
      });

    // Close price line
    const priceLine = d3
      .line<TechnicalIndicator>()
      .defined((d) => d.close != null)
      .x((d) => xScale(new Date(d.date)))
      .y((d) => yScale(d.close!));

    g.append('path')
      .datum(valid)
      .attr('d', priceLine)
      .attr('fill', 'none')
      .attr('stroke', '#10b981')
      .attr('stroke-width', 2);

    // Axes
    g.append('g')
      .attr('transform', `translate(0,${H})`)
      .call(d3.axisBottom(xScale).ticks(6).tickFormat(d3.timeFormat('%b %d') as never))
      .selectAll('text')
      .attr('fill', '#9ca3af');

    g.append('g')
      .call(d3.axisLeft(yScale).ticks(5).tickFormat((v) => `$${v}`))
      .selectAll('text')
      .attr('fill', '#9ca3af');

    g.selectAll('.domain').attr('stroke', '#374151');
    g.selectAll('.tick line').attr('stroke', '#374151');

    // Earnings date markers — vertical dashed lines with "E" label
    if (earningsDates.length > 0) {
      const [domainStart, domainEnd] = xScale.domain() as [Date, Date];
      earningsDates.forEach((ds) => {
        const dt = new Date(ds + 'T12:00:00'); // noon UTC to avoid timezone shift
        if (dt < domainStart || dt > domainEnd) return;
        const ex = xScale(dt);
        g.append('line')
          .attr('x1', ex).attr('x2', ex)
          .attr('y1', 0).attr('y2', H)
          .attr('stroke', '#facc15')
          .attr('stroke-width', 1)
          .attr('stroke-dasharray', '4,3')
          .attr('opacity', 0.7);
        g.append('text')
          .attr('x', ex + 3).attr('y', H - 6)
          .attr('fill', '#facc15').attr('font-size', 9).attr('font-weight', 700)
          .text('E');
      });
    }

    // Crosshair tooltip
    const tooltip = d3.select('body').select<HTMLDivElement>('#price-tooltip');
    const bisect = d3.bisector<TechnicalIndicator, Date>((d) => new Date(d.date)).left;

    const focus = g.append('g').style('display', 'none');
    focus.append('line')
      .attr('class', 'x-line')
      .attr('y1', 0).attr('y2', H)
      .attr('stroke', '#6b7280')
      .attr('stroke-dasharray', '3,3');
    focus.append('circle').attr('r', 4).attr('fill', '#10b981');

    g.append('rect')
      .attr('width', W).attr('height', H)
      .attr('fill', 'none')
      .attr('pointer-events', 'all')
      .on('mousemove', function (event) {
        const [mx] = d3.pointer(event);
        const x0 = xScale.invert(mx);
        const i = bisect(valid, x0, 1);
        const d = valid[Math.min(i, valid.length - 1)];
        focus.style('display', null);
        focus.select('.x-line').attr('transform', `translate(${xScale(new Date(d.date))},0)`);
        focus.select('circle')
          .attr('cx', xScale(new Date(d.date)))
          .attr('cy', yScale(d.close!));
        tooltip
          .style('display', 'block')
          .style('left', `${event.pageX + 12}px`)
          .style('top', `${event.pageY - 28}px`)
          .html(
            `<strong>${d.date}</strong><br/>` +
            `Close: <strong>$${d.close?.toFixed(2) ?? '—'}</strong>` +
            (d.ma50 ? `<br/>MA50: $${d.ma50.toFixed(2)}` : '') +
            (d.ma200 ? `<br/>MA200: $${d.ma200.toFixed(2)}` : ''),
          );
      })
      .on('mouseleave', () => {
        focus.style('display', 'none');
        tooltip.style('display', 'none');
      });
  }, [data, showMAs, showBB, height, earningsDates]);

  const latest = data[data.length - 1];
  const first  = data[0];
  const pctChange = latest?.close && first?.close
    ? ((latest.close - first.close) / first.close) * 100
    : null;

  return (
    <Box>
      <Stack direction="row" spacing={3} sx={{ mb: 1, px: 1, flexWrap: 'wrap' }}>
        {latest?.close != null && (
          <Typography variant="body2">
            Latest: <strong>${latest.close.toFixed(2)}</strong>
            {pctChange != null && (
              <span style={{ color: pctChange >= 0 ? '#10b981' : '#ef4444', marginLeft: 6 }}>
                {pctChange >= 0 ? '+' : ''}{pctChange.toFixed(2)}%
              </span>
            )}
          </Typography>
        )}
        {showBB && (
          <Typography variant="body2" sx={{ color: '#6366f1', opacity: 0.8 }}>
            ━ Bollinger Bands
          </Typography>
        )}
        {showMAs.ma30 && <Typography variant="body2" sx={{ color: MA_COLORS.ma30 }}>━ MA30</Typography>}
        {showMAs.ma50 && <Typography variant="body2" sx={{ color: MA_COLORS.ma50 }}>━ MA50</Typography>}
        {showMAs.ma200 && <Typography variant="body2" sx={{ color: MA_COLORS.ma200 }}>━ MA200</Typography>}
        {earningsDates.length > 0 && (
          <Typography variant="body2" sx={{ color: '#facc15' }}>┆ Earnings</Typography>
        )}
      </Stack>
      <svg ref={ref} style={{ width: '100%', display: 'block' }} />
    </Box>
  );
}
