/**
 * Card helpers.
 *
 * These format the figures a funder reads off a wall display, so the failure
 * mode is silent: a number that renders at the wrong scale or precision looks
 * exactly as authoritative as a correct one.
 */
import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));

function loadHelpers() {
  const src = fs.readFileSync(path.join(here, 'cards.js'), 'utf8');
  const win = {};
  new Function('window', src)(win);
  return win.PulseCards.helpers;
}

describe('usdCompact', () => {
  const { usdCompact } = loadHelpers();

  it('compacts millions and thousands', () => {
    expect(usdCompact(903088.25)).toBe('$903k');
    expect(usdCompact(1_650_000)).toBe('$1.65M');
    expect(usdCompact(28_000)).toBe('$28k');
  });

  it('keeps sub-$1k totals at the same scale as the rest of a ranked list', () => {
    // India's real by-country total was $196.24 against Nigeria's $460k. Cents
    // in one cell of a ranked list read as a rendering fault, not as a small
    // programme.
    expect(usdCompact(196.24)).toBe('$196');
    expect(usdCompact(999.7)).toBe('$1,000');
  });

  it('still shows cents below a dollar, where rounding would print $0', () => {
    expect(usdCompact(0.71)).toBe('$0.71');
    expect(usdCompact(0)).toBe('$0.00');
  });

  it('treats missing money as zero rather than NaN', () => {
    expect(usdCompact(null)).toBe('$0.00');
    expect(usdCompact(undefined)).toBe('$0.00');
  });
});
