/**
 * IVTermStructureChart — D3 bar chart of avg call/put IV vs DTE.
 * Shows whether near-term volatility is elevated (event risk / backwardation)
 * or whether the curve is in normal contango.
 */
import { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import type { OptionsExpiration } from '../../../api/securitiesTypes';

interface Props {
  expirations: OptionsExpiration[];
  height?: number;
}

function daysToExpiry(expiration: string): number {
  return Math.max(0, Math.round((new Date(expiration).getTime() - Date.now()) / 86_400_000));
}

export default function IVTermStructureChart({ expirations, height = 240 }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    if (!svgRef.current || expirations.length === 0) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    const margin = { top: 20, right: 24, bottom: 54, left: 52 };
    const width = svgRef.current.parentElement?.clientWidth ?? 640;
    const W = width - margin.left - margin.right;
    const H = height - margin.top - margin.bottom;

    svg.attr('width', width).attr('height', height);
    const g = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`);

    // Build data: one point per expiration with avg composite IV
    const data = expirations
      .filter((e) => e.avg_call_iv != null || e.avg_put_iv != null)
      .map((e) => {
        const callIV = e.avg_call_iv ?? 0;
        const putIV  = e.avg_put_iv  ?? 0;
        const count  = (e.avg_call_iv != null ? 1 : 0) + (e.avg_put_iv != null ? 1 : 0);
        const avgIV  = count > 0 ? (callIV + putIV) / count : 0;
        return {
          expiration: e.expiration,
          dte:        daysToExpiry(e.expiration),
          callIV,
          putIV,
          avgIV,
        };
      })
      .filter((d) => d.avgIV > 0)
      .slice(0, 20); // cap at 20 expirations for readability

    if (data.length === 0) return;

    const xScale = d3.scaleBand()
      .domain(data.map((d) => d.expiration))
      .range([0, W])
      .padding(0.3);

    const yMax = d3.max(data, (d) => Math.max(d.callIV, d.putIV)) ?? 100;
    const yScale = d3.scaleLinear()
      .domain([0, yMax * 1.1])
      .range([H, 0])
      .nice();

    // Grid
    g.append('g')
      .call(d3.axisLeft(yScale).tickSize(-W).tickFormat(() => '').ticks(5))
      .selectAll('line')
      .attr('stroke', '#1f2937')
      .attr('stroke-dasharray', '3,3');
    g.select('.domain').remove();

    const bw = xScale.bandwidth();

    // Call IV bars (blue)
    g.selectAll('.bar-call')
      .data(data)
      .enter().append('rect')
      .attr('class', 'bar-call')
      .attr('x', (d) => (xScale(d.expiration) ?? 0))
      .attr('y', (d) => yScale(d.callIV))
      .attr('width', bw / 2)
      .attr('height', (d) => Math.max(0, H - yScale(d.callIV)))
      .attr('fill', '#3b82f6')
      .attr('opacity', 0.75);

    // Put IV bars (amber)
    g.selectAll('.bar-put')
      .data(data)
      .enter().append('rect')
      .attr('class', 'bar-put')
      .attr('x', (d) => (xScale(d.expiration) ?? 0) + bw / 2)
      .attr('y', (d) => yScale(d.putIV))
      .attr('width', bw / 2)
      .attr('height', (d) => Math.max(0, H - yScale(d.putIV)))
      .attr('fill', '#f59e0b')
      .attr('opacity', 0.75);

    // Avg composite IV line
    const lineGen = d3.line<typeof data[0]>()
      .x((d) => (xScale(d.expiration) ?? 0) + bw / 2)
      .y((d) => yScale(d.avgIV))
      .curve(d3.curveMonotoneX);

    g.append('path')
      .datum(data)
      .attr('d', lineGen)
      .attr('fill', 'none')
      .attr('stroke', '#a855f7')
      .attr('stroke-width', 1.5)
      .attr('stroke-dasharray', '4,2');

    // X axis — show DTE labels, thin out when dense
    const maxLabels = Math.floor(W / 50);
    const every = Math.max(1, Math.ceil(data.length / maxLabels));
    const tickVals = data.filter((_, i) => i % every === 0).map((d) => d.expiration);

    g.append('g')
      .attr('transform', `translate(0,${H})`)
      .call(d3.axisBottom(xScale).tickValues(tickVals).tickFormat((d) => {
        const item = data.find((x) => x.expiration === d);
        return item ? `${item.dte}d` : d as string;
      }))
      .selectAll('text')
      .attr('fill', '#9ca3af')
      .attr('font-size', 10)
      .attr('transform', 'rotate(-35)')
      .style('text-anchor', 'end');

    g.select('.domain').attr('stroke', '#374151');

    // Y axis
    g.append('g')
      .call(d3.axisLeft(yScale).ticks(5).tickFormat((v) => `${v}%`))
      .selectAll('text')
      .attr('fill', '#9ca3af')
      .attr('font-size', 10);

    // Tooltip
    const tooltip = d3.select('#price-tooltip');

    const hitTargets = g.selectAll<SVGRectElement, typeof data[0]>('.hit')
      .data(data)
      .enter().append('rect')
      .attr('class', 'hit')
      .attr('x', (d) => xScale(d.expiration) ?? 0)
      .attr('y', 0)
      .attr('width', bw)
      .attr('height', H)
      .attr('fill', 'transparent');

    hitTargets
      .on('mousemove', (event, d) => {
        tooltip
          .style('display', 'block')
          .style('left', `${event.clientX + 14}px`)
          .style('top', `${event.clientY - 28}px`)
          .html(
            `<b>${d.expiration}</b> (${d.dte}d)<br/>` +
            `Call IV: <b>${d.callIV.toFixed(1)}%</b><br/>` +
            `Put IV: <b>${d.putIV.toFixed(1)}%</b><br/>` +
            `Avg IV: <b>${d.avgIV.toFixed(1)}%</b>`,
          );
      })
      .on('mouseleave', () => tooltip.style('display', 'none'));
  }, [expirations, height]);

  if (expirations.length === 0) return null;

  return <svg ref={svgRef} style={{ display: 'block', width: '100%' }} />;
}
