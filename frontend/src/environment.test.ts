import { afterEach, describe, expect, it } from 'vitest';
import { getQuantuiEnvironment } from './environment';

function setTag(content: string) {
  const meta = document.createElement('meta');
  meta.name = 'quantui-environment';
  meta.content = content;
  document.head.appendChild(meta);
}

afterEach(() => {
  document.head.querySelectorAll('meta[name="quantui-environment"]').forEach((m) => m.remove());
});

describe('getQuantuiEnvironment', () => {
  it.each(['test', 'prod'] as const)('reads %s from the injected tag', (value) => {
    setTag(value);
    expect(getQuantuiEnvironment()).toBe(value);
  });

  it('is null with no tag, as in Vite dev', () => {
    expect(getQuantuiEnvironment()).toBeNull();
  });

  it.each(['', 'TEST', 'staging', 'test '])('is null for the unknown value %j', (value) => {
    setTag(value);
    expect(getQuantuiEnvironment()).toBeNull();
  });

  it('reads the document it is given', () => {
    const doc = document.implementation.createHTMLDocument('x');
    const meta = doc.createElement('meta');
    meta.name = 'quantui-environment';
    meta.content = 'test';
    doc.head.appendChild(meta);
    expect(getQuantuiEnvironment(doc)).toBe('test');
    expect(getQuantuiEnvironment()).toBeNull();
  });
});
