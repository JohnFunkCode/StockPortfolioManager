/**
 * Risk graph for a two-leg vertical spread, rendered from chat directives.
 * Solid line: P/L at expiration (hockey stick). Dashed line: BS-priced
 * "value today" P/L. Everything — tradable numbers AND curves — comes from
 * the backend response (include_curves; the math's single home is
 * quantcore/analytics/options_math.py). This card only draws.
 */
import { useEffect, useRef, useState } from 'react';
import * as d3 from 'd3';
import { Alert, Box, Chip, CircularProgress, Stack, Typography } from '@mui/material';
import { useVerticalSpread } from '../../hooks/useSecurities';
import { usePricePolling } from '../../hooks/useSymbols';
import { useDirectiveInteractions } from '../../chat/DirectiveInteractions';

const HEIGHT = 240;
const MARGIN = { top: 12, right: 16, bottom: 28, left: 56 };

interface Props {
  ticker: string;
  expiration: string;
  long_strike: number;
  short_strike: number;
  kind: string;
}

function daysToExpiration(expiration: string): number {
  const exp = new Date(`${expiration}T21:00:00Z`); // ~4pm ET close
  return Math.max(0, (exp.getTime() - Date.now()) / 86_400_000);
}

export default function SpreadPayoffCard({
  ticker,
  expiration,
  long_strike,
  short_strike,
  kind,
}: Props) {
  const { data, isLoading, error } = useVerticalSpread(
    ticker,
    expiration,
    long_strike,
    short_strike,
    kind,
  );
  const { data: priceData } = usePricePolling(ticker);
  const spot = priceData?.price ?? 0;
  const svgRef = useRef<SVGSVGElement>(null);
  const [containerWidth, setContainerWidth] = useState(0);
  const interactions = useDirectiveInteractions();
  const [selectedStrike, setSelectedStrike] = useState<number | null>(null);
  // Once this instance's gesture has been sent, the mark is part of the
  // answered conversation: render it from the consumed record, immutably.
  const lockedStrike = (() => {
    for (let i = interactions.consumed.length - 1; i >= 0; i--) {
      const strike = interactions.consumed[i].payload.strike;
      if (typeof strike === 'number') return strike;
    }
    return null;
  })();
  const shownStrike = interactions.locked ? lockedStrike : selectedStrike;

  // Track the container's real width (bubble sizing settles after first
  // paint, and expand/collapse changes it) so the chart always fills it.
  useEffect(() => {
    const parent = svgRef.current?.parentElement;
    if (!parent) return;
    setContainerWidth(parent.clientWidth);
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver((entries) => {
      const w = Math.floor(entries[0]?.contentRect.width ?? 0);
      if (w > 0) setContainerWidth(w);
    });
    observer.observe(parent);
    return () => observer.disconnect();
  }, [data]);

  useEffect(() => {
    if (!svgRef.current || !data?.legs || !data.curves) return;
    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    const width = containerWidth || svgRef.current.parentElement?.clientWidth || 360;
    svg.attr('width', width).attr('height', HEIGHT);

    const { prices, expiry, now } = data.curves;
    const perContract = (v: number) => v * 100;

    const x = d3
      .scaleLinear()
      .domain([prices[0], prices[prices.length - 1]])
      .range([MARGIN.left, width - MARGIN.right]);
    const yExtent = [
      Math.min(...expiry.map(perContract)) * 1.15,
      Math.max(...expiry.map(perContract)) * 1.15,
    ];
    const y = d3.scaleLinear().domain(yExtent).range([HEIGHT - MARGIN.bottom, MARGIN.top]);

    // Shaded profit/loss zones relative to the expiration curve.
    const area = d3
      .area<number>()
      .x((_, i) => x(prices[i]))
      .y0(y(0))
      .y1((_, i) => y(perContract(expiry[i])));
    svg
      .append('path')
      .datum(expiry.map((v) => Math.max(v, 0)))
      .attr('d', area)
      .attr('fill', '#10b981')
      .attr('opacity', 0.13)
      .attr('clip-path', null);
    svg
      .append('path')
      .datum(expiry.map((v) => Math.min(v, 0)))
      .attr('d', area)
      .attr('fill', '#ef4444')
      .attr('opacity', 0.13);

    // Axes.
    svg
      .append('g')
      .attr('transform', `translate(0,${HEIGHT - MARGIN.bottom})`)
      .call(d3.axisBottom(x).ticks(6).tickFormat((v) => `$${v}`))
      .attr('color', '#6b7280')
      .style('font-size', '10px');
    svg
      .append('g')
      .attr('transform', `translate(${MARGIN.left},0)`)
      .call(d3.axisLeft(y).ticks(5).tickFormat((v) => `$${v}`))
      .attr('color', '#6b7280')
      .style('font-size', '10px');

    // Zero P/L line.
    svg
      .append('line')
      .attr('x1', MARGIN.left)
      .attr('x2', width - MARGIN.right)
      .attr('y1', y(0))
      .attr('y2', y(0))
      .attr('stroke', '#6b7280')
      .attr('stroke-width', 1)
      .attr('opacity', 0.6);

    const lineFor = (values: number[]) =>
      d3
        .line<number>()
        .x((_, i) => x(prices[i]))
        .y((v) => y(perContract(v)))(values);

    // Now-curve (dashed) under the expiration curve (solid).
    svg
      .append('path')
      .attr('d', lineFor(now))
      .attr('fill', 'none')
      .attr('stroke', '#00e5ff')
      .attr('stroke-width', 1.5)
      .attr('stroke-dasharray', '5,4')
      .attr('opacity', 0.9);
    svg
      .append('path')
      .attr('d', lineFor(expiry))
      .attr('fill', 'none')
      .attr('stroke', '#f9fafb')
      .attr('stroke-width', 2);

    // Vertical markers: strikes, breakeven, spot.
    const marker = (price: number, color: string, label: string, dash = '3,3') => {
      if (price < prices[0] || price > prices[prices.length - 1]) return;
      svg
        .append('line')
        .attr('x1', x(price))
        .attr('x2', x(price))
        .attr('y1', MARGIN.top)
        .attr('y2', HEIGHT - MARGIN.bottom)
        .attr('stroke', color)
        .attr('stroke-width', 1)
        .attr('stroke-dasharray', dash)
        .attr('opacity', 0.85);
      svg
        .append('text')
        .attr('x', x(price))
        .attr('y', MARGIN.top + 9)
        .attr('text-anchor', 'middle')
        .attr('fill', color)
        .style('font-size', '9px')
        .text(label);
    };
    marker(data.legs.long.strike, '#3b82f6', `L ${data.legs.long.strike}`);
    marker(data.legs.short.strike, '#f59e0b', `S ${data.legs.short.strike}`);
    marker(data.breakeven, '#a78bfa', `BE ${data.breakeven}`);
    if (spot > 0) marker(spot, '#ff2d78', `spot ${spot.toFixed(2)}`, '1,0');
    if (shownStrike != null) marker(shownStrike, '#22d3ee', `sel ${shownStrike}`, '6,3');

    // Backchannel: click a price on the chart to select a strike — snapped to
    // $0.50 — and attach it to the next message (context mode).
    if (interactions.enabled) {
      svg.style('cursor', 'crosshair').on('click', (event) => {
        const [mx] = d3.pointer(event);
        if (mx < MARGIN.left || mx > width - MARGIN.right) return;
        const strike = Math.round(x.invert(mx) * 2) / 2;
        setSelectedStrike(strike);
        interactions.interact('select_strike', { strike });
      });
    }
  }, [data, spot, expiration, containerWidth, interactions, shownStrike]);

  if (isLoading) {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 2 }}>
        <CircularProgress size={18} />
        <Typography variant="body2" color="text.secondary">
          Pricing {ticker} {long_strike}/{short_strike} {kind} spread…
        </Typography>
      </Box>
    );
  }
  if (error || !data?.legs) {
    return (
      <Alert severity="error" data-testid="spread-payoff-error">
        Couldn&apos;t price the {ticker} {long_strike}/{short_strike} {kind} spread
        {error instanceof Error ? ` — ${error.message}` : ''}.
      </Alert>
    );
  }
  if (!data.curves) {
    // Curves are server-computed (issue #79); a legs-bearing response without
    // them means the API predates include_curves support.
    return (
      <Alert severity="error" data-testid="spread-payoff-error">
        Priced the {ticker} spread, but the server returned no payoff curves —
        the API is likely older than this UI.
      </Alert>
    );
  }

  const dte = Math.round(daysToExpiration(expiration));
  const debit = data.mid_debit || data.debit;
  const isCredit = debit < 0;
  const chips: [string, string][] = [
    [isCredit ? 'Credit' : 'Debit', `$${Math.abs(debit).toFixed(2)}`],
    ['Max profit', `$${data.max_profit.toFixed(2)}`],
    ['Max loss', `$${data.max_loss.toFixed(2)}`],
    ['Breakeven', `$${data.breakeven.toFixed(2)}`],
    ['R:R', data.risk_reward != null ? `${data.risk_reward.toFixed(2)}:1` : '—'],
    ['DTE', `${dte}`],
  ];

  return (
    <Box data-testid="spread-payoff-card">
      <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
        {ticker} {data.legs.long.strike}/{data.legs.short.strike} {data.kind} spread · {expiration}
      </Typography>
      <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap sx={{ mb: 1 }}>
        {chips.map(([label, value]) => (
          <Chip key={label} size="small" variant="outlined" label={`${label}: ${value}`} sx={{ fontSize: 11 }} />
        ))}
      </Stack>
      {(data.warnings?.length ?? 0) > 0 && (
        <Alert severity="warning" data-testid="spread-payoff-warning" sx={{ mb: 1, py: 0 }}>
          {data.warnings!.join(' · ')}
        </Alert>
      )}
      <Box sx={{ width: '100%' }}>
        <svg ref={svgRef} data-testid="spread-payoff-chart" />
      </Box>
      <Typography variant="caption" color="text.secondary">
        Solid: P/L at expiration · Dashed: value today (BS, per-leg IV) · per contract (×100)
        {interactions.enabled && ' · Click the chart to select a strike'}
        {interactions.locked && shownStrike != null && ' · Selection locked (answered)'}
      </Typography>
      {interactions.enabled && selectedStrike != null && (
        <Stack direction="row" spacing={0.5} sx={{ mt: 0.5 }}>
          {(['long', 'short'] as const).map((leg) => (
            <Chip
              key={leg}
              size="small"
              color="primary"
              variant="outlined"
              data-testid={`reprice-${leg}`}
              label={`Move ${leg} leg → $${selectedStrike}`}
              onClick={() =>
                interactions.interact(
                  'reprice_leg',
                  { leg, strike: selectedStrike },
                  `Reprice the ${ticker} ${expiration} ${kind} spread with the ${leg} leg moved to $${selectedStrike} (other leg unchanged), and compare it to the current ${long_strike}/${short_strike}.`,
                )
              }
              sx={{ fontSize: 11 }}
            />
          ))}
        </Stack>
      )}
    </Box>
  );
}
