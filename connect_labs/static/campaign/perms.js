// perms.js — central role-based access control model (matches spec permission matrix)
(function () {
  // Roles in display order
  const ROLES = [
    {
      id: 'admin',
      name: 'Campaign Administrator',
      short: 'Campaign admin',
      color: '#16006D',
      desc: 'Full access across all areas and actions.',
    },
    {
      id: 'payment',
      name: 'Payment Administrator',
      short: 'Payment admin',
      color: '#3843D0',
      desc: 'View campaign data; review and approve worker payments.',
    },
    {
      id: 'compliance',
      name: 'Compliance Administrator',
      short: 'Compliance admin',
      color: '#01A2A9',
      desc: 'View, create, edit and approve KYC & verification records.',
    },
    {
      id: 'operations',
      name: 'Operations Manager',
      short: 'Operations',
      color: '#9A5183',
      desc: 'Create, edit and manage activities; view operational data.',
    },
    {
      id: 'reporting',
      name: 'Reporting User',
      short: 'Reporting',
      color: '#6C757D',
      desc: 'Read-only access to dashboards and reports, with export.',
    },
  ];

  // Modules (functional areas)
  const MODULES = [
    { id: 'overview', label: 'Overview' },
    { id: 'workers', label: 'Workers' },
    { id: 'kyc', label: 'KYC & Verification' },
    { id: 'payments', label: 'Payments' },
    { id: 'activities', label: 'Activities' },
    { id: 'planning', label: 'Microplanning & Budget' },
    { id: 'reporting', label: 'Reporting & Monitoring' },
    { id: 'users', label: 'User Management' },
  ];

  // Permission verbs
  const VERBS = [
    'view',
    'create',
    'edit',
    'approve',
    'manage',
    'export',
    'delete',
  ];
  const FULL = [
    'view',
    'create',
    'edit',
    'approve',
    'manage',
    'export',
    'delete',
  ];

  // Matrix: role -> module -> [verbs]. Sourced directly from the spec table.
  const MATRIX = {
    admin: {
      overview: FULL,
      workers: FULL,
      kyc: FULL,
      payments: FULL,
      activities: FULL,
      planning: FULL,
      reporting: FULL,
      users: FULL,
    },
    payment: {
      overview: ['view'],
      workers: ['view'],
      kyc: [],
      payments: ['view', 'approve'],
      activities: [],
      planning: [],
      reporting: ['view', 'export'],
      users: [],
    },
    compliance: {
      overview: ['view'],
      workers: ['view'],
      kyc: ['view', 'create', 'edit', 'approve'],
      payments: [],
      activities: [],
      planning: [],
      reporting: ['view', 'export'],
      users: [],
    },
    operations: {
      overview: ['view'],
      workers: ['view'],
      kyc: ['view'],
      payments: ['view'],
      activities: ['view', 'create', 'edit', 'manage'],
      planning: ['view'],
      reporting: ['view', 'export'],
      users: [],
    },
    reporting: {
      overview: ['view'],
      workers: ['view'],
      kyc: ['view'],
      payments: ['view'],
      activities: ['view'],
      planning: ['view'],
      reporting: ['view', 'export'],
      users: [],
    },
  };

  // Connection Settings is System Administrator only (Campaign Administrator).
  const CONNECTIONS_ROLES = ['admin'];

  // Training MANAGEMENT is Campaign Administrator only; viewing the hub is public
  // (handled outside RBAC). Mirrors the server matrix in rbac.py.
  const TRAINING_ROLES = ['admin'];

  const roleIdByName = (name) =>
    (ROLES.find((r) => r.name === name || r.short === name) || ROLES[0]).id;

  // can(roleName, moduleId, verb) -> boolean
  function can(roleName, moduleId, verb = 'view') {
    const id = roleIdByName(roleName);
    if (moduleId === 'connections') return CONNECTIONS_ROLES.includes(id);
    if (moduleId === 'training') return TRAINING_ROLES.includes(id);
    const mods = MATRIX[id] || {};
    return (mods[moduleId] || []).includes(verb);
  }
  // summarized access label for the matrix display
  function accessLabel(roleId, moduleId) {
    const v = (MATRIX[roleId] || {})[moduleId] || [];
    if (v.length === 0) return 'No Access';
    if (v.length >= 7) return 'Full Access';
    const map = {
      view: 'View',
      create: 'Create',
      edit: 'Edit',
      approve: 'Approve',
      manage: 'Manage',
      export: 'Export',
      delete: 'Delete',
    };
    return v.map((x) => map[x]).join(', ');
  }

  window.CUT_RBAC = {
    ROLES,
    MODULES,
    VERBS,
    MATRIX,
    can,
    accessLabel,
    roleIdByName,
    CONNECTIONS_ROLES,
    TRAINING_ROLES,
  };
})();
