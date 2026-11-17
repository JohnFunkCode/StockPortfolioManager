import { describe, expect, it } from 'vitest';

import { SALE_SOURCES, brokerWords, stopDetail, trailPercent } from './stopWords';
import { stopRow } from '../../testUtils';
import type { Stop } from '../../api/stops';

const stop = (overrides: object = {}) => stopRow(overrides) as unknown as Stop;

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

  it('says where a stop mirrored from a broker was placed', () => {
    const text = stopDetail(stop({
      at_broker: true, brokerage: 'Schwab', broker_order_type: 'stop_limit', broker_quantity: 100,
    }));
    expect(text).toContain('Placed at Schwab as a stop-limit order for 100 shares. Not checked yet');
  });

  it('says a reached broker stop should have filled', () => {
    const text = stopDetail(stop({
      status: 'triggered', triggered_session: '2026-06-16', trigger_price: 44.1, at_broker: true,
    }));
    expect(text).toContain('Placed at a broker. That order should have filled: record the sale.');
    expect(text).not.toContain('Nothing was sold');
  });

  it('shows a method it does not know as it came', () => {
    expect(stopDetail(stop({ method: 'chandelier' }))).toContain('chandelier');
  });
});

describe('brokerWords', () => {
  it('is empty for a stop that exists only here', () => {
    expect(brokerWords(stop())).toBe('');
  });

  it('says only what the owner gave', () => {
    expect(brokerWords(stop({ at_broker: true }))).toBe('Placed at a broker.');
    expect(brokerWords(stop({ at_broker: true, broker_order_type: 'trailing_stop' })))
      .toBe('Placed at a broker as a trailing stop order.');
    expect(brokerWords(stop({ at_broker: true, brokerage: 'Fidelity', broker_quantity: 2.5 })))
      .toBe('Placed at Fidelity for 2.5 shares.');
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
