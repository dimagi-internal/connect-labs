// Pure card-resolution logic for the pages surface bundle.
// No DOM / React imports so it is unit-testable under vitest (Node, CommonJS).

const SHIPPED_CARD_TYPES = ['stat', 'audit_summary', 'list', 'summary'];

/**
 * Decide how a card payload should render.
 * - render_code present  -> JSX escape hatch
 * - known card_type      -> its shipped renderer
 * - unknown card_type    -> the default shipped renderer
 * @returns {{mode: 'render_code'|'shipped', cardType: string}}
 */
function resolveCard(payload) {
  if (payload && payload.render_code) {
    return { mode: 'render_code', cardType: payload.card_type };
  }
  const cardType =
    payload && SHIPPED_CARD_TYPES.includes(payload.card_type)
      ? payload.card_type
      : 'default';
  return { mode: 'shipped', cardType };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { SHIPPED_CARD_TYPES, resolveCard };
}
