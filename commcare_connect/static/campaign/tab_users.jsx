// tab_users.jsx — System Administration › User Management (RBAC)
const { useState: useStateU } = React;

const SEED_USERS = [
  {
    id: 'U1',
    name: 'Amara Okafor',
    email: 'amara.okafor@dimagi.com',
    role: 'admin',
    scope: 'All regions',
    status: 'active',
    last: 'Just now',
    you: true,
  },
  {
    id: 'U2',
    name: 'Tunde Balogun',
    email: 'tunde.balogun@dimagi.com',
    role: 'payment',
    scope: 'Kano, Kaduna',
    status: 'active',
    last: '12 min ago',
  },
  {
    id: 'U3',
    name: 'Ngozi Eze',
    email: 'ngozi.eze@partner.org',
    role: 'compliance',
    scope: 'All regions',
    status: 'active',
    last: '1 hr ago',
  },
  {
    id: 'U4',
    name: 'Fatima Bello',
    email: 'fatima.bello@moh.gov.ng',
    role: 'operations',
    scope: 'All regions',
    status: 'active',
    last: 'Yesterday',
  },
  {
    id: 'U5',
    name: 'David Mensah',
    email: 'david.mensah@dimagi.com',
    role: 'payment',
    scope: 'Sokoto, Bauchi',
    status: 'active',
    last: '3 hr ago',
  },
  {
    id: 'U6',
    name: 'Aisha Lawal',
    email: 'aisha.lawal@partner.org',
    role: 'compliance',
    scope: 'Borno',
    status: 'pending',
    last: '—',
  },
  {
    id: 'U7',
    name: 'Grace Adeyemi',
    email: 'grace.adeyemi@donor.org',
    role: 'reporting',
    scope: 'All regions',
    status: 'active',
    last: '2 days ago',
  },
  {
    id: 'U8',
    name: 'Samuel Okoro',
    email: 'samuel.okoro@dimagi.com',
    role: 'operations',
    scope: 'Kano',
    status: 'inactive',
    last: '3 weeks ago',
  },
  {
    id: 'U9',
    name: 'Joseph Idoko',
    email: 'joseph.idoko@partner.org',
    role: 'reporting',
    scope: 'All regions',
    status: 'deactivated',
    last: '2 months ago',
  },
];

const AUDIT_LOG = [
  {
    at: 'Jun 3, 2026 · 09:41',
    user: 'Amara Okafor',
    action: 'Approved 8 worker payments',
    module: 'Payments',
    ip: '102.89.x.x',
  },
  {
    at: 'Jun 3, 2026 · 09:12',
    user: 'Ngozi Eze',
    action: 'Approved KYC for W10342',
    module: 'KYC',
    ip: '197.210.x.x',
  },
  {
    at: 'Jun 3, 2026 · 08:55',
    user: 'Amara Okafor',
    action: "Changed Samuel Okoro's role to Operations Manager",
    module: 'User Management',
    ip: '102.89.x.x',
  },
  {
    at: 'Jun 2, 2026 · 17:30',
    user: 'Tunde Balogun',
    action: 'Logged in',
    module: 'Authentication',
    ip: '105.112.x.x',
  },
  {
    at: 'Jun 2, 2026 · 16:04',
    user: 'Amara Okafor',
    action: 'Invited aisha.lawal@partner.org (Compliance Administrator)',
    module: 'User Management',
    ip: '102.89.x.x',
  },
  {
    at: 'Jun 2, 2026 · 14:48',
    user: 'Fatima Bello',
    action: 'Created activity ACT-05',
    module: 'Activities',
    ip: '154.113.x.x',
  },
  {
    at: 'Jun 2, 2026 · 11:20',
    user: 'Amara Okafor',
    action: 'Deactivated user Joseph Idoko',
    module: 'User Management',
    ip: '102.89.x.x',
  },
];

function UserManagement({ density, role }) {
  const D = window.CUT_DATA;
  const RBAC = window.CUT_RBAC;
  const toast = useToast();
  const [users, setUsers] = useStateU(SEED_USERS.map((u) => ({ ...u })));
  const [view, setView] = useStateU('users');
  const [q, setQ] = useStateU('');
  const [roleF, setRoleF] = useStateU('all');
  const [statusF, setStatusF] = useStateU('all');
  const [invite, setInvite] = useStateU(false);
  const canManage = RBAC.can(role, 'users', 'manage');

  const roleMeta = (id) =>
    RBAC.ROLES.find((r) => r.id === id) || { name: id, color: '#6C757D' };
  const filtered = users.filter(
    (u) =>
      (roleF === 'all' || u.role === roleF) &&
      (statusF === 'all' || u.status === statusF) &&
      (!q ||
        u.name.toLowerCase().includes(q.toLowerCase()) ||
        u.email.toLowerCase().includes(q.toLowerCase())),
  );
  const statusTone = {
    active: 'success',
    pending: 'warning',
    inactive: 'neutral',
    deactivated: 'danger',
  };

  const setUserRole = (id, r) => {
    setUsers((us) => us.map((u) => (u.id === id ? { ...u, role: r } : u)));
    toast('Role updated');
  };
  const setStatus = (id, s) => {
    setUsers((us) => us.map((u) => (u.id === id ? { ...u, status: s } : u)));
    toast(
      'Account ' + (s === 'active' ? 'reactivated' : s),
      s === 'deactivated' ? 'danger' : 'success',
    );
  };

  return (
    <Page max={1340}>
      <PageHead
        eyebrow="System Administration"
        title="User Management"
        sub="Manage who can access the Campaign Utility Tool and what they can do. Access is enforced through role-based access control (RBAC)."
        actions={
          canManage ? (
            <Button icon="user-plus" onClick={() => setInvite(true)}>
              Invite user
            </Button>
          ) : (
            <Badge tone="neutral">Read-only for {role}</Badge>
          )
        }
      />

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, 1fr)',
          gap: 14,
          marginBottom: 18,
        }}
      >
        <Stat
          label="Total users"
          value={users.length}
          icon="users-gear"
          sub={users.filter((u) => u.status === 'active').length + ' active'}
        />
        <Stat
          label="Pending activation"
          value={users.filter((u) => u.status === 'pending').length}
          icon="envelope"
          delta="Invited"
          deltaTone="warning"
        />
        <Stat
          label="Administrators"
          value={
            users.filter((u) => u.role === 'admin' && u.status === 'active')
              .length
          }
          icon="user-shield"
        />
        <Stat
          label="Roles defined"
          value={RBAC.ROLES.length}
          icon="shield-halved"
        />
      </div>

      <div style={{ marginBottom: 16 }}>
        <PillTabs
          active={view}
          onChange={setView}
          tabs={[
            { id: 'users', label: 'Users', icon: 'users' },
            {
              id: 'roles',
              label: 'Roles & permissions',
              icon: 'shield-halved',
            },
            { id: 'audit', label: 'Activity log', icon: 'clock-rotate-left' },
          ]}
        />
      </div>

      {view === 'users' && (
        <>
          <div
            style={{
              display: 'flex',
              justifyContent: 'flex-end',
              gap: 10,
              marginBottom: 14,
              flexWrap: 'wrap',
            }}
          >
            <div style={{ position: 'relative' }}>
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search name or email…"
                style={{
                  fontFamily: 'inherit',
                  fontSize: 13,
                  padding: '8px 12px 8px 30px',
                  border: '1px solid ' + CUTC.border,
                  borderRadius: 8,
                  width: 220,
                  outline: 'none',
                  color: CUTC.purple,
                }}
              />
              <i
                className="fa fa-search"
                style={{
                  position: 'absolute',
                  left: 11,
                  top: '50%',
                  transform: 'translateY(-50%)',
                  fontSize: 11,
                  color: CUTC.muted,
                }}
              ></i>
            </div>
            <Select
              value={roleF}
              onChange={(e) => setRoleF(e.target.value)}
              style={{
                width: 'auto',
                fontSize: 13,
                padding: '8px 30px 8px 12px',
              }}
            >
              <option value="all">All roles</option>
              {RBAC.ROLES.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </Select>
            <Select
              value={statusF}
              onChange={(e) => setStatusF(e.target.value)}
              style={{
                width: 'auto',
                fontSize: 13,
                padding: '8px 30px 8px 12px',
              }}
            >
              <option value="all">All statuses</option>
              {['active', 'pending', 'deactivated'].map((s) => (
                <option key={s} value={s}>
                  {s[0].toUpperCase() + s.slice(1)}
                </option>
              ))}
            </Select>
          </div>
          <Card padding={0}>
            <Table
              density={density}
              columns={[
                { label: 'User' },
                { label: 'Role' },
                { label: 'Region scope' },
                { label: 'Status' },
                { label: 'Last login' },
                { label: '', align: 'right', width: 130 },
              ]}
            >
              {filtered.map((u) => (
                <Row key={u.id}>
                  <Cell>
                    <div
                      style={{ display: 'flex', alignItems: 'center', gap: 11 }}
                    >
                      <Avatar name={u.name} />
                      <div>
                        <div
                          style={{
                            fontWeight: 600,
                            color: CUTC.purple,
                            display: 'flex',
                            alignItems: 'center',
                            gap: 8,
                          }}
                        >
                          {u.name}
                          {u.you && <Badge tone="primary">You</Badge>}
                        </div>
                        <div style={{ fontSize: 12, color: CUTC.muted }}>
                          {u.email}
                        </div>
                      </div>
                    </div>
                  </Cell>
                  <Cell>
                    {canManage && !u.you ? (
                      <select
                        value={u.role}
                        onChange={(e) => setUserRole(u.id, e.target.value)}
                        style={{
                          fontFamily: 'inherit',
                          fontSize: 12.5,
                          fontWeight: 600,
                          color: roleMeta(u.role).color,
                          border: '1px solid ' + CUTC.border,
                          borderRadius: 7,
                          padding: '5px 8px',
                          background: '#fff',
                          cursor: 'pointer',
                        }}
                      >
                        {RBAC.ROLES.map((r) => (
                          <option key={r.id} value={r.id}>
                            {r.name}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <span
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 7,
                          fontWeight: 600,
                          color: roleMeta(u.role).color,
                          fontSize: 13,
                        }}
                      >
                        <span
                          style={{
                            width: 8,
                            height: 8,
                            borderRadius: 2,
                            background: roleMeta(u.role).color,
                          }}
                        ></span>
                        {roleMeta(u.role).name}
                      </span>
                    )}
                  </Cell>
                  <Cell>{u.scope}</Cell>
                  <Cell>
                    <Badge
                      tone={statusTone[u.status]}
                      dot={u.status === 'active' || u.status === 'pending'}
                    >
                      {u.status[0].toUpperCase() + u.status.slice(1)}
                    </Badge>
                  </Cell>
                  <Cell style={{ color: CUTC.muted, fontSize: 12.5 }}>
                    {u.last}
                  </Cell>
                  <Cell align="right">
                    {canManage && !u.you ? (
                      u.status === 'deactivated' || u.status === 'inactive' ? (
                        <Button
                          size="sm"
                          variant="secondary"
                          icon="rotate-left"
                          onClick={() => setStatus(u.id, 'active')}
                        >
                          Reactivate
                        </Button>
                      ) : (
                        <Button
                          size="sm"
                          variant="ghost"
                          icon="ban"
                          onClick={() => setStatus(u.id, 'deactivated')}
                        >
                          Deactivate
                        </Button>
                      )
                    ) : (
                      <span style={{ color: CUTC.faint }}>—</span>
                    )}
                  </Cell>
                </Row>
              ))}
            </Table>
            {filtered.length === 0 && (
              <Empty icon="users" title="No users match" />
            )}
          </Card>
        </>
      )}

      {view === 'roles' && <RoleMatrix />}

      {view === 'audit' && (
        <Card padding={0}>
          <div
            style={{
              padding: '16px 22px',
              borderBottom: '1px solid ' + CUTC.border,
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}
          >
            <SectionTitle sub="Login history, role changes and administrative actions">
              Activity & audit log
            </SectionTitle>
            <Button size="sm" variant="secondary" icon="download">
              Export log
            </Button>
          </div>
          <Table
            density={density}
            columns={[
              { label: 'Timestamp' },
              { label: 'User' },
              { label: 'Action' },
              { label: 'Module' },
              { label: 'IP' },
            ]}
          >
            {AUDIT_LOG.map((e, i) => (
              <Row key={i}>
                <Cell mono style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
                  {e.at}
                </Cell>
                <Cell strong>{e.user}</Cell>
                <Cell>{e.action}</Cell>
                <Cell>
                  <Badge tone="neutral">{e.module}</Badge>
                </Cell>
                <Cell mono style={{ fontSize: 12, color: CUTC.muted }}>
                  {e.ip}
                </Cell>
              </Row>
            ))}
          </Table>
        </Card>
      )}

      <InviteUserModal
        open={invite}
        onClose={() => setInvite(false)}
        roles={RBAC.ROLES}
        regions={D.REGIONS}
        onInvite={(u) => {
          setUsers((us) => [
            { id: 'U' + (us.length + 1), status: 'pending', last: '—', ...u },
            ...us,
          ]);
          toast('Activation email sent to ' + u.email);
          setInvite(false);
        }}
      />
    </Page>
  );
}

function RoleMatrix() {
  const RBAC = window.CUT_RBAC;
  const labelTone = (label) =>
    label === 'Full Access'
      ? 'success'
      : label === 'No Access'
      ? 'neutral'
      : 'info';
  return (
    <Card padding={0}>
      <div
        style={{
          padding: '16px 22px',
          borderBottom: '1px solid ' + CUTC.border,
        }}
      >
        <SectionTitle sub="What each predefined role is allowed to do per module. Assign roles per user on the Users tab.">
          Roles & permissions matrix
        </SectionTitle>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table
          style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}
        >
          <thead>
            <tr>
              <th
                style={{
                  textAlign: 'left',
                  padding: '14px 22px',
                  fontSize: 11,
                  fontWeight: 600,
                  color: CUTC.muted,
                  letterSpacing: '.06em',
                  textTransform: 'uppercase',
                  borderBottom: '1px solid ' + CUTC.border,
                  position: 'sticky',
                  left: 0,
                  background: '#fff',
                }}
              >
                Module
              </th>
              {RBAC.ROLES.map((r) => (
                <th
                  key={r.id}
                  style={{
                    padding: '14px 14px',
                    borderBottom: '1px solid ' + CUTC.border,
                    textAlign: 'left',
                    minWidth: 150,
                  }}
                >
                  <div
                    style={{ display: 'flex', alignItems: 'center', gap: 7 }}
                  >
                    <span
                      style={{
                        width: 9,
                        height: 9,
                        borderRadius: 2,
                        background: r.color,
                      }}
                    ></span>
                    <span
                      style={{
                        fontSize: 12,
                        fontWeight: 600,
                        color: CUTC.purple,
                      }}
                    >
                      {r.name}
                    </span>
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {RBAC.MODULES.map((m) => (
              <tr key={m.id}>
                <td
                  style={{
                    padding: '12px 22px',
                    borderBottom: '1px solid ' + CUTC.borderSoft,
                    color: CUTC.purple,
                    fontWeight: 600,
                    position: 'sticky',
                    left: 0,
                    background: '#fff',
                  }}
                >
                  {m.label}
                </td>
                {RBAC.ROLES.map((r) => {
                  const label = RBAC.accessLabel(r.id, m.id);
                  return (
                    <td
                      key={r.id}
                      style={{
                        padding: '12px 14px',
                        borderBottom: '1px solid ' + CUTC.borderSoft,
                      }}
                    >
                      <Badge tone={labelTone(label)}>{label}</Badge>
                    </td>
                  );
                })}
              </tr>
            ))}
            <tr>
              <td
                style={{
                  padding: '12px 22px',
                  borderBottom: '1px solid ' + CUTC.borderSoft,
                  color: CUTC.purple,
                  fontWeight: 600,
                  position: 'sticky',
                  left: 0,
                  background: '#fff',
                }}
              >
                Connection Settings
              </td>
              {RBAC.ROLES.map((r) => (
                <td
                  key={r.id}
                  style={{
                    padding: '12px 14px',
                    borderBottom: '1px solid ' + CUTC.borderSoft,
                  }}
                >
                  <Badge tone={r.id === 'admin' ? 'success' : 'neutral'}>
                    {r.id === 'admin' ? 'Full Access' : 'No Access'}
                  </Badge>
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
      <div
        style={{
          padding: '14px 22px',
          borderTop: '1px solid ' + CUTC.border,
          background: CUTC.surface,
          fontSize: 12.5,
          color: CUTC.muted,
          display: 'flex',
          gap: 8,
          alignItems: 'center',
        }}
      >
        <i className="fa fa-circle-info" style={{ color: 'var(--accent)' }}></i>
        Roles are enforced throughout the tool — e.g. a Compliance Administrator
        cannot approve payments. Use the role switcher (top-right) to preview
        any role.
      </div>
    </Card>
  );
}

function InviteUserModal({ open, onClose, roles, regions, onInvite }) {
  const [name, setName] = useStateU('');
  const [email, setEmail] = useStateU('');
  const [role, setRoleI] = useStateU('reporting');
  const [scope, setScope] = useStateU('All regions');
  React.useEffect(() => {
    if (open) {
      setName('');
      setEmail('');
      setRoleI('reporting');
      setScope('All regions');
    }
  }, [open]);
  const valid = name.trim().length > 1 && /.+@.+\..+/.test(email);
  return (
    <Modal
      open={open}
      onClose={onClose}
      width={540}
      title="Invite user"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            icon="paper-plane"
            disabled={!valid}
            onClick={() => onInvite({ name, email, role, scope })}
          >
            Send invitation
          </Button>
        </>
      }
    >
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
        <Field label="Full name">
          <TextInput
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Chidi Nwosu"
          />
        </Field>
        <Field label="Email">
          <TextInput
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="name@org.com"
          />
        </Field>
      </div>
      <Field label="Role">
        <Select value={role} onChange={(e) => setRoleI(e.target.value)}>
          {roles.map((r) => (
            <option key={r.id} value={r.id}>
              {r.name}
            </option>
          ))}
        </Select>
      </Field>
      <Field
        label="Region scope"
        help="Limit this user to specific regions, or grant access to all."
      >
        <Select value={scope} onChange={(e) => setScope(e.target.value)}>
          <option>All regions</option>
          {regions.map((r) => (
            <option key={r.id}>{r.name}</option>
          ))}
        </Select>
      </Field>
      <div
        style={{
          background: 'var(--accent-soft)',
          borderRadius: 10,
          padding: '12px 14px',
          fontSize: 12.5,
          color: 'var(--accent-dark)',
          display: 'flex',
          gap: 10,
          alignItems: 'flex-start',
        }}
      >
        <i className="fa fa-envelope-circle-check" style={{ marginTop: 2 }}></i>
        <span>
          The user receives an email invitation with a secure activation link to
          set their own password before first login. Their permissions follow
          the role above.
        </span>
      </div>
    </Modal>
  );
}

Object.assign(window, { UserManagement });
