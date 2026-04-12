/**
 * OptionsChainChart — D3 heatmap of open interest by strike × call/put side,
 * plus a summary table for the selected expiration.
 */
import { useEffect, useRef, useState } from 'react';
import * as d3 from 'd3';
import {
  Box,
  Chip,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import type { OptionsExpiration, OptionsSnapshot } from '../../../api/securitiesTypes';

// ---------------------------------------------------------------------------
// Narrative summary builder
// ---------------------------------------------------------------------------

function buildSummary(snapshot: OptionsSnapshot, exp: OptionsExpiration): string[] {
  const lines: string[] = [];
  const { price, bb_upper, bb_lower, bb_middle } = snapshot;
  const {
    put_call_ratio: pcr, total_call_oi, total_put_oi,
    total_call_vol, total_put_vol, avg_call_iv, avg_put_iv,
    contracts, expiration,
  } = exp;

  // --- P/C ratio sentiment ---
  if (pcr != null) {
    if (pcr > 1.5) {
      lines.push(`Put/call OI ratio is elevated at ${pcr.toFixed(2)}, indicating strong bearish hedging or outright put buying for the ${expiration} expiry.`);
    } else if (pcr > 1.0) {
      lines.push(`Put/call OI ratio of ${pcr.toFixed(2)} shows a modest bearish skew — more open put positions than calls for ${expiration}.`);
    } else if (pcr < 0.5) {
      lines.push(`Put/call OI ratio is low at ${pcr.toFixed(2)}, reflecting bullish positioning with call open interest dominating for ${expiration}.`);
    } else {
      lines.push(`Put/call OI ratio of ${pcr.toFixed(2)} is near neutral, suggesting balanced sentiment for the ${expiration} expiry.`);
    }
  }

  // --- OI imbalance ---
  if (total_call_oi != null && total_put_oi != null && total_call_oi + total_put_oi > 0) {
    const callPct = Math.round(total_call_oi / (total_call_oi + total_put_oi) * 100);
    if (callPct >= 60) {
      lines.push(`Call open interest makes up ${callPct}% of total OI (${total_call_oi.toLocaleString()} calls vs ${total_put_oi.toLocaleString()} puts), pointing to bullish positioning.`);
    } else if (callPct <= 40) {
      lines.push(`Put open interest dominates at ${100 - callPct}% of total OI (${total_put_oi.toLocaleString()} puts vs ${total_call_oi.toLocaleString()} calls), suggesting hedging or bearish bets.`);
    }
  }

  // --- Volume skew ---
  if (total_call_vol != null && total_put_vol != null && total_call_vol + total_put_vol > 0) {
    const volRatio = total_call_vol / (total_put_vol || 1);
    if (volRatio > 1.5) {
      lines.push(`Today's volume skews bullish: ${total_call_vol.toLocaleString()} call contracts traded vs ${total_put_vol.toLocaleString()} puts (${volRatio.toFixed(1)}× call volume).`);
    } else if (volRatio < 0.67) {
      lines.push(`Today's volume skews bearish: ${total_put_vol.toLocaleString()} put contracts traded vs ${total_call_vol.toLocaleString()} calls (${(1 / volRatio).toFixed(1)}× put volume).`);
    }
  }

  // --- IV differential ---
  if (avg_call_iv != null && avg_put_iv != null) {
    const diff = avg_put_iv - avg_call_iv;
    if (diff > 5) {
      lines.push(`Put IV averages ${avg_put_iv.toFixed(1)}% vs call IV at ${avg_call_iv.toFixed(1)}% — a ${diff.toFixed(1)}pp put skew, typical of downside-protection demand.`);
    } else if (diff < -5) {
      lines.push(`Call IV averages ${avg_call_iv.toFixed(1)}% vs put IV at ${avg_put_iv.toFixed(1)}% — calls are priced richer, suggesting speculative upside demand.`);
    } else {
      lines.push(`Call IV (${avg_call_iv.toFixed(1)}%) and put IV (${avg_put_iv.toFixed(1)}%) are closely matched, implying symmetric expectations.`);
    }
  }

  // --- Price vs Bollinger Bands ---
  if (bb_upper != null && bb_lower != null && bb_middle != null && price > 0) {
    if (price >= bb_upper) {
      lines.push(`At $${price.toFixed(2)}, the stock is trading at or above the upper Bollinger Band ($${bb_upper.toFixed(2)}), a potential overbought signal.`);
    } else if (price <= bb_lower) {
      lines.push(`At $${price.toFixed(2)}, the stock is trading at or below the lower Bollinger Band ($${bb_lower.toFixed(2)}), a potential oversold signal.`);
    } else {
      const pctThrough = ((price - bb_lower) / (bb_upper - bb_lower) * 100).toFixed(0);
      lines.push(`Price ($${price.toFixed(2)}) is ${pctThrough}% of the way through the Bollinger Band range ($${bb_lower.toFixed(2)}–$${bb_upper.toFixed(2)}), near the midline ($${bb_middle.toFixed(2)}).`);
    }
  }

  // --- ITM / OTM mix ---
  const itmCalls = contracts.filter((c) => c.kind === 'call' && c.in_the_money).length;
  const itmPuts  = contracts.filter((c) => c.kind === 'put'  && c.in_the_money).length;
  const totalContracts = contracts.length;
  if (totalContracts > 0) {
    lines.push(`${totalContracts} contracts shown across ${[...new Set(contracts.map((c) => c.strike))].length} strikes; ${itmCalls} call${itmCalls !== 1 ? 's' : ''} and ${itmPuts} put${itmPuts !== 1 ? 's' : ''} are currently in the money.`);
  }

  return lines;
}

interface Props {
  snapshot: OptionsSnapshot;
}

const MARGIN = { top: 20, right: 20, bottom: 60, left: 60 };

function OIHeatmap({ expiration }: { expiration: OptionsExpiration }) {
  const ref = useRef<SVGSVGElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    const svg = d3.select(ref.current);
    svg.selectAll('*').remove();

    const { contracts } = expiration;
    if (contracts.length === 0) return;

    const strikes = Array.from(new Set(contracts.map((c) => c.strike))).sort(d3.ascending);
    const kinds: ('call' | 'put')[] = ['call', 'put'];

    const width = ref.current.parentElement!.clientWidth;
    const W = width - MARGIN.left - MARGIN.right;
    const H = Math.max(80, strikes.length * 22);
    svg.attr('width', width).attr('height', H + MARGIN.top + MARGIN.bottom);

    const xScale = d3.scaleBand().domain(kinds).range([0, W]).padding(0.1);
    const yScale = d3.scaleBand()
      .domain(strikes.map(String))
      .range([0, H])
      .padding(0.05);

    const maxOI = d3.max(contracts, (c) => c.open_interest ?? 0) ?? 1;
    const colorCall = d3.scaleSequential(d3.interpolateBlues).domain([0, maxOI]);
    const colorPut  = d3.scaleSequential(d3.interpolateReds).domain([0, maxOI]);

    const g = svg.append('g').attr('transform', `translate(${MARGIN.left},${MARGIN.top})`);

    g.selectAll('.cell')
      .data(contracts)
      .join('rect')
      .attr('x', (c) => xScale(c.kind)!)
      .attr('y', (c) => yScale(String(c.strike))!)
      .attr('width', xScale.bandwidth())
      .attr('height', yScale.bandwidth())
      .attr('fill', (c) =>
        c.kind === 'call'
          ? colorCall(c.open_interest ?? 0)
          : colorPut(c.open_interest ?? 0),
      )
      .attr('rx', 3)
      .attr('opacity', (c) => (c.in_the_money ? 1 : 0.7));

    // ITM indicator
    contracts.filter((c) => c.in_the_money).forEach((c) => {
      g.append('text')
        .attr('x', xScale(c.kind)! + xScale.bandwidth() - 4)
        .attr('y', yScale(String(c.strike))! + yScale.bandwidth() / 2)
        .attr('dy', '0.35em')
        .attr('text-anchor', 'end')
        .attr('fill', '#fff')
        .attr('font-size', 9)
        .text('ITM');
    });

    // OI labels
    g.selectAll('.oi-label')
      .data(contracts.filter((c) => (c.open_interest ?? 0) > 0))
      .join('text')
      .attr('x', (c) => xScale(c.kind)! + xScale.bandwidth() / 2)
      .attr('y', (c) => yScale(String(c.strike))! + yScale.bandwidth() / 2)
      .attr('dy', '0.35em')
      .attr('text-anchor', 'middle')
      .attr('fill', '#fff')
      .attr('font-size', 9)
      .attr('pointer-events', 'none')
      .text((c) => c.open_interest != null ? `${c.open_interest.toLocaleString()}` : '');

    // X axis
    g.append('g')
      .attr('transform', `translate(0,${H})`)
      .call(d3.axisBottom(xScale).tickFormat((d) => d.toUpperCase()))
      .selectAll('text')
      .attr('fill', '#9ca3af');

    // Y axis (strikes)
    g.append('g')
      .call(d3.axisLeft(yScale).tickFormat((d) => `$${d}`))
      .selectAll('text')
      .attr('fill', '#9ca3af')
      .attr('font-size', 10);

    g.selectAll('.domain').attr('stroke', '#374151');
    g.selectAll('.tick line').attr('stroke', '#374151');
  }, [expiration]);

  return <svg ref={ref} style={{ width: '100%', display: 'block' }} />;
}

export default function OptionsChainChart({ snapshot }: Props) {
  const [selectedIdx, setSelectedIdx] = useState(0);

  if (!snapshot.expirations || snapshot.expirations.length === 0) {
    return (
      <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
        No options data available for this security.
      </Typography>
    );
  }

  const exp = snapshot.expirations[selectedIdx];
  const calls = exp.contracts.filter((c) => c.kind === 'call');
  const puts  = exp.contracts.filter((c) => c.kind === 'put');
  const summaryLines = buildSummary(snapshot, exp);

  return (
    <Box>
      {/* Narrative summary */}
      {summaryLines.length > 0 && (
        <Paper
          variant="outlined"
          sx={{ p: 2, mb: 2, borderColor: 'divider', bgcolor: 'background.default' }}
        >
          <Typography variant="subtitle2" sx={{ mb: 1, color: 'text.secondary' }}>
            Interpretation
          </Typography>
          <Stack spacing={0.75}>
            {summaryLines.map((line, i) => (
              <Typography key={i} variant="body2" sx={{ color: 'text.primary', lineHeight: 1.6 }}>
                {line}
              </Typography>
            ))}
          </Stack>
        </Paper>
      )}

      {/* Expiration selector */}
      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mb: 2 }}>
        {snapshot.expirations.map((e, i) => (
          <Chip
            key={e.expiration}
            label={e.expiration}
            onClick={() => setSelectedIdx(i)}
            color={i === selectedIdx ? 'primary' : 'default'}
            variant={i === selectedIdx ? 'filled' : 'outlined'}
            size="small"
          />
        ))}
      </Stack>

      {/* Summary stats */}
      <Stack direction="row" spacing={3} sx={{ mb: 2, flexWrap: 'wrap' }}>
        <Typography variant="body2">
          P/C Ratio:{' '}
          <strong style={{ color: (exp.put_call_ratio ?? 0) > 1 ? '#ef4444' : '#10b981' }}>
            {exp.put_call_ratio?.toFixed(2) ?? '—'}
          </strong>
        </Typography>
        <Typography variant="body2">
          Call OI: <strong style={{ color: '#3b82f6' }}>{exp.total_call_oi?.toLocaleString() ?? '—'}</strong>
        </Typography>
        <Typography variant="body2">
          Put OI: <strong style={{ color: '#ef4444' }}>{exp.total_put_oi?.toLocaleString() ?? '—'}</strong>
        </Typography>
        <Typography variant="body2">
          Avg Call IV: <strong>{exp.avg_call_iv != null ? `${exp.avg_call_iv.toFixed(1)}%` : '—'}</strong>
        </Typography>
        <Typography variant="body2">
          Avg Put IV: <strong>{exp.avg_put_iv != null ? `${exp.avg_put_iv.toFixed(1)}%` : '—'}</strong>
        </Typography>
      </Stack>

      {/* OI Heatmap */}
      <Typography variant="subtitle2" gutterBottom>Open Interest Heatmap</Typography>
      <OIHeatmap expiration={exp} />

      {/* Detailed contract table */}
      <Typography variant="subtitle2" sx={{ mt: 3, mb: 1 }}>ATM Contracts</Typography>
      <Paper variant="outlined" sx={{ overflow: 'auto' }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Strike</TableCell>
              <TableCell align="right" sx={{ color: '#3b82f6' }}>Call Last</TableCell>
              <TableCell align="right" sx={{ color: '#3b82f6' }}>Call Bid</TableCell>
              <TableCell align="right" sx={{ color: '#3b82f6' }}>Call Ask</TableCell>
              <TableCell align="right" sx={{ color: '#3b82f6' }}>Call OI</TableCell>
              <TableCell align="right" sx={{ color: '#3b82f6' }}>Call IV</TableCell>
              <TableCell align="right" sx={{ color: '#ef4444' }}>Put Last</TableCell>
              <TableCell align="right" sx={{ color: '#ef4444' }}>Put Bid</TableCell>
              <TableCell align="right" sx={{ color: '#ef4444' }}>Put Ask</TableCell>
              <TableCell align="right" sx={{ color: '#ef4444' }}>Put OI</TableCell>
              <TableCell align="right" sx={{ color: '#ef4444' }}>Put IV</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {calls.map((call) => {
              const put = puts.find((p) => p.strike === call.strike);
              return (
                <TableRow
                  key={call.strike}
                  sx={{ bgcolor: call.in_the_money ? 'action.selected' : undefined }}
                >
                  <TableCell>
                    <strong>${call.strike}</strong>
                    {call.in_the_money ? (
                      <Chip label="ITM" size="small" color="success" sx={{ ml: 0.5, height: 16, fontSize: 9 }} />
                    ) : null}
                  </TableCell>
                  <TableCell align="right">{call.last_price != null ? `$${call.last_price.toFixed(2)}` : '—'}</TableCell>
                  <TableCell align="right">{call.bid != null ? `$${call.bid.toFixed(2)}` : '—'}</TableCell>
                  <TableCell align="right">{call.ask != null ? `$${call.ask.toFixed(2)}` : '—'}</TableCell>
                  <TableCell align="right">{call.open_interest?.toLocaleString() ?? '—'}</TableCell>
                  <TableCell align="right">{call.implied_vol != null ? `${call.implied_vol.toFixed(1)}%` : '—'}</TableCell>
                  <TableCell align="right">{put?.last_price != null ? `$${put.last_price.toFixed(2)}` : '—'}</TableCell>
                  <TableCell align="right">{put?.bid != null ? `$${put.bid.toFixed(2)}` : '—'}</TableCell>
                  <TableCell align="right">{put?.ask != null ? `$${put.ask.toFixed(2)}` : '—'}</TableCell>
                  <TableCell align="right">{put?.open_interest?.toLocaleString() ?? '—'}</TableCell>
                  <TableCell align="right">{put?.implied_vol != null ? `${put.implied_vol.toFixed(1)}%` : '—'}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Paper>
    </Box>
  );
}
