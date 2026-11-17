import { describe, expect, it } from 'vitest';

import { SALE_SOURCES, stopDetail, stopsBySymbol, trailPercent } from './stopWords';
import { lotRow, stopRow } from '../../testUtils';
import type { Stop } from '../../api/stops';
import type { Lot } from '../../api/portfolioTypes';

const stop = (overrides: object = {}) => stopRow(overrides) as unknown as Stop;
const lots = (trade_date: string | null = '2026-01-02') => [lotRow({ trade_date }) as unknown as Lot];

describe('stopsBySymbol', () => {
  it('shows a held symbol its active stop', () => {
    const shown = stopsBySymbol([stop({ stop_id: 3 })], { INTC: lots() });
    expect(shown.INTC.stop_id).toBe(3);
  });

  it('reads only the newest stop of a symbol', () => {
    const shown = stopsBySymbol(
      [stop({ stop_id: 9, status: 'cancelled' }), stop({ stop_id: 8 })],
      { INTC: lots() },
    );
    expect(shown).toEqual({});
  });

  it('shows no stop for a symbol the book does not hold', () => {
    expect(stopsBySymbol([stop()], {})).toEqual({});
    expect(stopsBySymbol([stop()], { INTC: [] })).toEqual({});
  });

  it('shows a reached stop only to a lot opened on or before its bar', () => {
    const reached = stop({ status: 'triggered', triggered_session: '2026-06-16', trigger_price: 44 });
    expect(stopsBySymbol([reached], { INTC: lots('2026-06-16') }).INTC).toBe(reached);
    expect(stopsBySymbol([reached], { INTC: lots('2026-06-17') })).toEqual({});
    expect(stopsBySymbol([reached], { INTC: lots(null) })).toEqual({});
  });

  it('shows no stop that was replaced, closed or cancelled', () => {
    for (const status of ['replaced', 'closed', 'cancelled']) {
      expect(stopsBySymbol([stop({ status })], { INTC: lots() })).toEqual({});
    }
  });
});

describe('stopDetail', () => {
  it('says how a fixed stop was chosen and that it has not been checked', () => {
    const text = stopDetail(stop({ method: 'technical' }));
    expect(text).toContain("A fixed level, the stop card's technical stop, set");
    expect(text).toContain('Not checked yet');
  });

  it('gives a trailing stop its percentage, its high and its last check', () => {
    const text = stopDetail(
      stop({ kind: 'trailing', trail_pct: 7.5, high_water_mark: 200, last_bar_checked: '2026-06-16' }),
    );
    expect(text).toContain('Trails 7.5% below a high of $200.00, typed by hand');
    expect(text).toContain('Last checked against the bar of');
  });

  it('names the fill session as the first check for a stop set by a buy at the open', () => {
    const text = stopDetail(stop({ first_session: '2026-06-16' }));
    expect(text).toContain('the first check is against the bar of');
    expect(text).toContain('the day its buy filled at the open');
    expect(text).not.toContain('next session to open');
  });

  it('says a reached stop sold nothing and is gone', () => {
    const text = stopDetail(
      stop({ status: 'triggered', triggered_session: '2026-06-16', trigger_price: 44.1 }),
    );
    expect(text).toContain('reached this stop at $44.10');
    expect(text).toContain('Nothing was sold');
  });

  it('does not say nothing was sold for a paper book, where the stop sold the position', () => {
    const text = stopDetail(
      stop({ status: 'triggered', triggered_session: '2026-06-16', trigger_price: 44.1 }),
      true,
    );
    expect(text).toContain('reached this stop at $44.10');
    expect(text).toContain('The position was sold at that price.');
    expect(text).not.toContain('Nothing was sold');
  });

  it('shows a method it does not know as it came', () => {
    expect(stopDetail(stop({ method: 'chandelier' }))).toContain('chandelier');
  });
});

describe('the small words', () => {
  it('writes a percentage as typed', () => {
    expect(trailPercent(8)).toBe('8%');
    expect(trailPercent(7.5)).toBe('7.5%');
    expect(trailPercent(null)).toBe('');
  });

  it('has a word for every source a sale can have', () => {
    expect(Object.keys(SALE_SOURCES).sort()).toEqual(
      ['ai', 'evaluator', 'harvester', 'manual', 'stop', 'watch'],
    );
  });
});
