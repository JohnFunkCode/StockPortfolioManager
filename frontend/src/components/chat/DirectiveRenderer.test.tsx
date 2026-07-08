import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

vi.mock('../../hooks/useSecurities', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useSecurities: () => ({
    data: { securities: [{ symbol: 'INTC', name: 'Intel' }] },
  }),
}));

import DirectiveRenderer from './DirectiveRenderer';
import type { RegistryEntry } from '../../chat/componentRegistry';

function Dummy({ ticker }: { ticker: string }) {
  return <div data-testid="dummy">dummy:{ticker}</div>;
}

function MultiProp(props: { ticker: string; long_strike: number; kind: string }) {
  return (
    <div data-testid="multi">
      {props.ticker}|{props.long_strike}|{props.kind}
    </div>
  );
}

function Bomb(_props: { ticker: string }): React.ReactNode {
  throw new Error('kaboom');
}

const stubRegistry: Record<string, RegistryEntry> = {
  signals: { component: Dummy, spec: { ticker: 'string' }, titled: true },
  bomb: { component: Bomb, spec: { ticker: 'string' } },
  multi: {
    component: MultiProp,
    spec: { ticker: 'string', long_strike: 'number', kind: 'string' },
  },
};

afterEach(cleanup);

describe('DirectiveRenderer', () => {
  it('renders the registered component with the ticker prop', () => {
    render(
      <DirectiveRenderer
        directive={{ component: 'signals', props: { ticker: 'intc' } }}
        registry={stubRegistry}
      />,
    );
    // Ticker is normalized to uppercase before reaching the component.
    expect(screen.getByTestId('dummy')).toHaveTextContent('dummy:INTC');
  });

  it('renders a symbol — name header for titled components', () => {
    render(
      <DirectiveRenderer
        directive={{ component: 'signals', props: { ticker: 'intc' } }}
        registry={stubRegistry}
      />,
    );
    expect(screen.getByTestId('directive-title')).toHaveTextContent('INTC — Intel');
  });

  it('falls back to the bare symbol when the name is unknown', () => {
    render(
      <DirectiveRenderer
        directive={{ component: 'signals', props: { ticker: 'ZZXX' } }}
        registry={stubRegistry}
      />,
    );
    expect(screen.getByTestId('directive-title')).toHaveTextContent('ZZXX');
    expect(screen.getByTestId('directive-title').textContent).not.toContain('—');
  });

  it('renders no header for untitled components', () => {
    render(
      <DirectiveRenderer
        directive={{
          component: 'multi',
          props: { ticker: 'intc', long_strike: 140.5, kind: 'call' },
        }}
        registry={stubRegistry}
      />,
    );
    expect(screen.queryByTestId('directive-title')).toBeNull();
  });

  it('passes all validated props through (ticker uppercased, numbers intact)', () => {
    render(
      <DirectiveRenderer
        directive={{
          component: 'multi',
          props: { ticker: 'intc', long_strike: 140.5, kind: 'call' },
        }}
        registry={stubRegistry}
      />,
    );
    expect(screen.getByTestId('multi')).toHaveTextContent('INTC|140.5|call');
  });

  it('falls back to an alert with the raw JSON for unknown components', () => {
    render(
      <DirectiveRenderer
        directive={{ component: 'mystery', props: { ticker: 'INTC' } }}
        registry={stubRegistry}
      />,
    );
    const fallback = screen.getByTestId('directive-fallback');
    expect(fallback.textContent).toContain('mystery');
    expect(screen.queryByTestId('dummy')).toBeNull();
  });

  it('falls back for invalid props without crashing', () => {
    render(
      <DirectiveRenderer
        directive={{ component: 'signals', props: { ticker: '' } }}
        registry={stubRegistry}
      />,
    );
    expect(screen.getByTestId('directive-fallback')).toBeInTheDocument();
  });

  it('contains a throwing component in its error boundary; siblings unaffected', () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      render(
        <>
          <DirectiveRenderer
            directive={{ component: 'bomb', props: { ticker: 'X' } }}
            registry={stubRegistry}
          />
          <DirectiveRenderer
            directive={{ component: 'signals', props: { ticker: 'ZS' } }}
            registry={stubRegistry}
          />
        </>,
      );
      expect(screen.getByTestId('directive-error')).toBeInTheDocument();
      expect(screen.getByTestId('dummy')).toHaveTextContent('dummy:ZS');
    } finally {
      consoleError.mockRestore();
    }
  });
});
