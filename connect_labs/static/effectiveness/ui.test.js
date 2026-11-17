// The number formats every effectiveness page shares.
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';

globalThis.window = globalThis.window || globalThis;
// eslint-disable-next-line no-eval
eval(readFileSync(new URL('./ui.js', import.meta.url), 'utf8'));
const { num, usd } = window.EffUI;

describe('num', () => {
  it('writes thousands and millions the short way', () => {
    expect(num(950)).toBe('950');
    expect(num(41_300)).toBe('41K');
    expect(num(1_200_000)).toBe('1.2M');
  });

  it('calls a number a hair under a million a million, not 1000K', () => {
    expect(num(999_999.9999)).toBe('1.0M');
    expect(num(999_600)).toBe('1.0M');
    expect(num(999_499)).toBe('999K');
  });

  it('shows nothing as a dash', () => {
    expect(num(null)).toBe('–');
  });
});

describe('usd', () => {
  it('writes thousands, millions and billions the short way', () => {
    expect(usd(60_000_000)).toBe('$60.0M');
    expect(usd(2_566)).toBe('$3K');
    expect(usd(1_250_000_000)).toBe('$1.25B');
  });
});
