import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import DirectiveRenderer from './DirectiveRenderer';
import type { RegistryEntry } from '../../chat/componentRegistry';

function Dummy({ ticker }: { ticker: string }) {
  return <div data-testid="dummy">dummy:{ticker}</div>;
}

function Bomb(_props: { ticker: string }): React.ReactNode {
  throw new Error('kaboom');
}

const stubRegistry: Record<string, RegistryEntry> = {
  signals: { component: Dummy },
  bomb: { component: Bomb },
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
