// Client mirror of connect_labs/supply/rbac.py — show/hide ONLY.
// The server matrix is the real gate. tests/test_rbac_contract.py parses this
// literal and asserts equality with ROLE_PERMS, so keep them in lockstep.
const SUPPLY_PERMS = {
  supplier: {
    org: ['view', 'edit'],
    eoi: ['view', 'submit'],
    bids: ['view', 'submit'],
    execution: ['view', 'report'],
    tokens: ['manage'],
  },
  reviewer: {
    eoi_review: ['view', 'decide'],
    registry: ['view'],
    scoring: ['view', 'score'],
    execution: ['view'],
  },
  procurement_admin: {
    eoi_review: ['view', 'decide'],
    registry: ['view'],
    scoring: ['view', 'score'],
    rounds: ['view', 'manage'],
    rfps: ['view', 'manage', 'award'],
    execution: ['view', 'resolve'],
    audit: ['view'],
  },
  gov_observer: { execution: ['view'] },
  funder: { execution: ['view'] },
};

function supplyCan(role, module, verb) {
  return ((SUPPLY_PERMS[role] || {})[module] || []).includes(verb);
}
