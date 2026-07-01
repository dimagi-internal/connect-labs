import { describe, it, expect } from 'vitest';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { resolveCard, SHIPPED_CARD_TYPES } = require('./pages-cards-helpers.js');

describe('resolveCard', () => {
  it('uses the JSX escape hatch when render_code is present', () => {
    const r = resolveCard({
      card_type: 'stat',
      render_code: 'return function Card(){}',
    });
    expect(r).toEqual({ mode: 'render_code', cardType: 'stat' });
  });

  it('uses the shipped renderer for a known card_type', () => {
    expect(resolveCard({ card_type: 'audit_summary' })).toEqual({
      mode: 'shipped',
      cardType: 'audit_summary',
    });
  });

  it('falls back to the default shipped renderer for an unknown card_type', () => {
    expect(resolveCard({ card_type: 'totally-unknown' })).toEqual({
      mode: 'shipped',
      cardType: 'default',
    });
  });

  it('exposes the shipped card types', () => {
    expect(SHIPPED_CARD_TYPES).toContain('stat');
    expect(SHIPPED_CARD_TYPES).toContain('summary');
  });
});
