import { describe, expect, it } from 'vitest';
import { COMPONENT_REGISTRY, validateDirective } from './componentRegistry';

// Keep this case table aligned with test_chat_protocol.py::TestValidateDirective —
// the frontend re-validates independently of the backend.
describe('COMPONENT_REGISTRY', () => {
  it('resolves all v1 component names', () => {
    for (const name of ['signals', 'live_price', 'price_chart']) {
      expect(COMPONENT_REGISTRY[name]?.component, name).toBeTypeOf('function');
    }
  });
});

describe('validateDirective', () => {
  it('accepts every registered component with a ticker prop', () => {
    for (const name of Object.keys(COMPONENT_REGISTRY)) {
      const verdict = validateDirective({ component: name, props: { ticker: 'INTC' } });
      expect(verdict.ok, name).toBe(true);
    }
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
