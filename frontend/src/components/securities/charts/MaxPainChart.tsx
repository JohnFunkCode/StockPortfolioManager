import { useRef, useEffect } from 'react';
import * as d3 from 'd3';
import type { PainPoint } from '../../../api/securitiesTypes';

interface Props {
  painCurve: PainPoint[];
  currentPrice: number;
  maxPainStrike: number | null;
  height?: number;
}

function formatPain(v: number): string {
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

export default function MaxPainChart({
  painCurve,
  currentPrice,
  maxPainStrike,
  height = 280,
}: Props) {
  const svgRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    if (!svgRef.current || painCurve.length === 0) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    const margin = { top: 24, right: 24, bottom: 54, left: 80 };
    const width = svgRef.current.parentElement?.clientWidth ?? 640;
    const innerW = width - margin.left - margin.right;
    const innerH = height - margin.top - margin.bottom;

    svg.attr('width', width).attr('height', height);
    const g = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`);

    const strikes = painCurve.map((d) => d.strike);
    const minS = d3.min(strikes)!;
    const maxS = d3.max(strikes)!;
    const step = strikes.length > 1 ? (maxS - minS) / (strikes.length - 1) : 1;

    const xScale = d3.scaleLinear()
      .domain([minS - step * 0.5, maxS + step * 0.5])
      .range([0, innerW]);

    const yScale = d3.scaleLinear()
      .domain([0, d3.max(painCurve, (d) => d.pain)! * 1.08])
      .range([innerH, 0])
      .nice();

    // Horizontal grid
    g.append('g')
      .call(d3.axisLeft(yScale).tickSize(-innerW).tickFormat(() => '').ticks(5))
      .selectAll('line')
      .attr('stroke', '#1f2937')
      .attr('stroke-dasharray', '3,3');
    g.select('.domain').remove();

    // Bars
    const barW = Math.max(2, (innerW / strikes.length) * 0.8);
    g.selectAll('.bar')
      .data(painCurve)
      .enter()
      .append('rect')
      .attr('class', 'bar')
      .attr('x', (d) => xScale(d.strike) - barW / 2)
      .attr('y', (d) => yScale(d.pain))
      .attr('width', barW)
      .attr('height', (d) => Math.max(0, innerH - yScale(d.pain)))
      .attr('fill', (d) => (d.strike === maxPainStrike ? '#f59e0b' : '#3b82f6'))
      .attr('opacity', (d) => (d.strike === maxPainStrike ? 1.0 : 0.55));

    // Current price vertical line
    const cpX = xScale(currentPrice);
    if (cpX >= 0 && cpX <= innerW) {
      g.append('line')
        .attr('x1', cpX).attr('x2', cpX)
        .attr('y1', 0).attr('y2', innerH)
        .attr('stroke', '#10b981')
        .attr('stroke-width', 1.5)
        .attr('stroke-dasharray', '5,4');
      g.append('text')
        .attr('x', cpX + 4).attr('y', 12)
        .attr('fill', '#10b981').attr('font-size', 10)
        .text(`$${currentPrice.toFixed(0)}`);
    }

    // Max pain vertical line
    if (maxPainStrike != null) {
      const mpX = xScale(maxPainStrike);
      g.append('line')
        .attr('x1', mpX).attr('x2', mpX)
        .attr('y1', 0).attr('y2', innerH)
        .attr('stroke', '#f59e0b')
        .attr('stroke-width', 2)
        .attr('stroke-dasharray', '6,3');
      g.append('text')
        .attr('x', mpX + 4).attr('y', 26)
        .attr('fill', '#f59e0b').attr('font-size', 10).attr('font-weight', 600)
        .text(`Max Pain $${maxPainStrike}`);
    }

    // X axis — thin out tick labels when too many strikes
    const maxLabels = Math.floor(innerW / 52);
    const every = Math.max(1, Math.ceil(strikes.length / maxLabels));
    const tickVals = strikes.filter((_, i) => i % every === 0);

    g.append('g')
      .attr('transform', `translate(0,${innerH})`)
      .call(
        d3.axisBottom(xScale)
          .tickValues(tickVals)
          .tickFormat((d) => `$${d}`)
      )
      .selectAll('text')
      .attr('fill', '#9ca3af')
      .attr('font-size', 10)
      .attr('transform', 'rotate(-40)')
      .style('text-anchor', 'end');

    g.select('.domain').attr('stroke', '#374151');

    // Y axis
    g.append('g')
      .call(d3.axisLeft(yScale).ticks(5).tickFormat((d) => formatPain(d as number)))
      .selectAll('text')
      .attr('fill', '#9ca3af')
      .attr('font-size', 10);

    // Tooltip
    const tooltip = d3.select('#price-tooltip');
    g.selectAll<SVGRectElement, PainPoint>('.bar')
      .on('mousemove', (event, d) => {
        tooltip
          .style('display', 'block')
          .style('left', `${event.clientX + 14}px`)
          .style('top', `${event.clientY - 28}px`)
          .html(
            `Strike: <b>$${d.strike}</b><br/>` +
            `Total Pain: <b>${formatPain(d.pain)}</b>` +
            (d.strike === maxPainStrike
              ? '<br/><span style="color:#f59e0b">★ Max Pain</span>'
              : '')
          );
      })
      .on('mouseleave', () => tooltip.style('display', 'none'));
  }, [painCurve, currentPrice, maxPainStrike, height]);

  if (painCurve.length === 0) return null;

  return <svg ref={svgRef} style={{ display: 'block', width: '100%' }} />;
}
