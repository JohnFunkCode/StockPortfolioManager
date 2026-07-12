import { describe, expect, it } from 'vitest';
import { COMPONENT_REGISTRY, validateDirective } from './componentRegistry';

// Keep this case table aligned with test_chat_protocol.py::TestValidateDirective —
// the frontend re-validates independently of the backend.
describe('COMPONENT_REGISTRY', () => {
  it('resolves all registered component names', () => {
    for (const name of ['signals', 'live_price', 'price_chart', 'spread_payoff']) {
      expect(COMPONENT_REGISTRY[name]?.component, name).toBeTypeOf('function');
    }
  });
});

const SPREAD_PROPS = {
  ticker: 'INTC',
  expiration: '2026-08-21',
  long_strike: 140,
  short_strike: 160.5,
  kind: 'call',
};

describe('validateDirective', () => {
  it('accepts every ticker-only component with a ticker prop', () => {
    for (const name of ['signals', 'live_price', 'price_chart']) {
      const verdict = validateDirective({ component: name, props: { ticker: 'INTC' } });
      expect(verdict.ok, name).toBe(true);
    }
  });

  it('accepts a fully-specified spread_payoff directive', () => {
    expect(validateDirective({ component: 'spread_payoff', props: SPREAD_PROPS }).ok).toBe(true);
  });

  it('rejects spread_payoff with a missing strike', () => {
    const { short_strike: _omit, ...rest } = SPREAD_PROPS;
    const verdict = validateDirective({ component: 'spread_payoff', props: rest });
    expect(verdict.ok).toBe(false);
    if (!verdict.ok) expect(verdict.reason).toContain('short_strike');
  });

  it('rejects spread_payoff with a string strike', () => {
    expect(
      validateDirective({
        component: 'spread_payoff',
        props: { ...SPREAD_PROPS, long_strike: '140' },
      }).ok,
    ).toBe(false);
  });

  it('rejects spread_payoff with a NaN strike', () => {
    expect(
      validateDirective({
        component: 'spread_payoff',
        props: { ...SPREAD_PROPS, long_strike: NaN },
      }).ok,
    ).toBe(false);
  });

  it('rejects spread_payoff with extra props', () => {
    expect(
      validateDirective({
        component: 'spread_payoff',
        props: { ...SPREAD_PROPS, leverage: 10 },
      }).ok,
    ).toBe(false);
  });

  it('rejects ticker-only components given spread props', () => {
    expect(validateDirective({ component: 'signals', props: SPREAD_PROPS }).ok).toBe(false);
  });

  it('rejects unknown components with a reason', () => {
    const verdict = validateDirective({ component: 'nuclear_launch', props: { ticker: 'X' } });
    expect(verdict.ok).toBe(false);
    if (!verdict.ok) expect(verdict.reason).toContain('nuclear_launch');
  });

  it('rejects missing ticker', () => {
    expect(validateDirective({ component: 'signals', props: {} }).ok).toBe(false);
  });

  it('rejects empty or whitespace ticker', () => {
    expect(validateDirective({ component: 'signals', props: { ticker: '' } }).ok).toBe(false);
    expect(validateDirective({ component: 'signals', props: { ticker: '   ' } }).ok).toBe(false);
  });

  it('rejects non-string ticker', () => {
    expect(validateDirective({ component: 'signals', props: { ticker: 42 } }).ok).toBe(false);
  });

  it('strictly rejects unexpected extra props', () => {
    const verdict = validateDirective({
      component: 'signals',
      props: { ticker: 'INTC', explode: true },
    });
    expect(verdict.ok).toBe(false);
    if (!verdict.ok) expect(verdict.reason).toContain('explode');
  });

  it('rejects non-object props', () => {
    expect(
      validateDirective({ component: 'signals', props: 'INTC' as unknown as Record<string, unknown> }).ok,
    ).toBe(false);
  });
});
