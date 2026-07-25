/* Root shell: reads the inline bootstrap payload, renders role-shaped nav,
   routes tabs. No client store — after any mutation we re-fetch the bootstrap
   and re-render from server state. */

const BOOTSTRAP = JSON.parse(
  document.getElementById('supply-bootstrap').textContent,
);

const TABS = [
  // supplier
  {
    key: 'home',
    label: 'Dashboard',
    component: () => SupplierHome,
    roles: ['supplier'],
  },
  {
    key: 'org',
    label: 'Organisation',
    component: () => OrgTab,
    roles: ['supplier'],
  },
  {
    key: 'eoi',
    label: 'Expressions of interest',
    component: () => EOITab,
    roles: ['supplier'],
  },
  {
    key: 'bids',
    label: 'Solicitations & bids',
    component: () => BidsTab,
    roles: ['supplier'],
  },
  {
    key: 'ops',
    label: 'Operations',
    component: () => OpsTab,
    roles: ['supplier'],
  },
  {
    key: 'integration',
    label: 'Integration',
    component: () => IntegrationTab,
    roles: ['supplier'],
  },
  // staff
  {
    key: 'console',
    label: 'Dashboard',
    component: () => ConsoleHome,
    roles: ['procurement_admin', 'reviewer'],
  },
  {
    key: 'rounds',
    label: 'EOI rounds & review',
    component: () => RoundsTab,
    roles: ['procurement_admin', 'reviewer'],
  },
  {
    key: 'registry',
    label: 'Supplier registry',
    component: () => RegistryTab,
    roles: ['procurement_admin', 'reviewer'],
  },
  {
    key: 'rfps',
    label: 'Solicitations',
    component: () => RFPsTab,
    roles: ['procurement_admin', 'reviewer'],
  },
  {
    key: 'command',
    label: 'Command centre',
    component: () => CommandTab,
    roles: ['procurement_admin', 'reviewer'],
  },
  // read-only stakeholder surfaces
  {
    key: 'gov',
    label: 'Country overview',
    component: () => GovTab,
    roles: ['gov_observer'],
  },
  {
    key: 'funder',
    label: 'Funding & delivery',
    component: () => FunderTab,
    roles: ['funder'],
  },
];

function visibleTabs(role) {
  return TABS.filter((t) => t.roles.includes(role));
}

function App() {
  const [world, setWorld] = useState(BOOTSTRAP);
  const [tab, setTab] = useState(visibleTabs(BOOTSTRAP.role)[0]?.key);
  const [toast, setToast] = useState(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const fresh = await supplyGet('/supply/api/bootstrap/');
    setWorld(fresh);
    return fresh;
  }, []);

  // Every mutation funnels through here: run it, refresh server state, toast.
  const act = useCallback(
    async (fn, successMessage) => {
      setBusy(true);
      try {
        const result = await fn();
        await refresh();
        if (successMessage) setToast({ message: successMessage, tone: 'good' });
        return result;
      } catch (err) {
        setToast({ message: err.message, tone: 'bad' });
        return null;
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  const tabs = visibleTabs(world.role);
  const active = tabs.find((t) => t.key === tab) || tabs[0];
  const Component = active ? active.component() : null;

  const ctx = { world, refresh, act, busy, setToast };

  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar-brand">
          <span className="brand-mark">Operation End Starvation</span>
          <span className="brand-sub">Supply &amp; Procurement</span>
        </div>
        <div className="topbar-user">
          <span className="role-chip">{roleLabel(world.role)}</span>
          <span className="user-name">
            {world.org ? world.org.legal_name : world.user.name}
          </span>
          <form method="post" action="/supply/logout/">
            <input
              type="hidden"
              name="csrfmiddlewaretoken"
              value={supplyCsrfToken()}
            />
            <button type="submit" className="btn btn-secondary btn-sm">
              Sign out
            </button>
          </form>
        </div>
      </header>

      <div className="body">
        <nav className="sidenav">
          {tabs.map((t) => (
            <button
              type="button"
              key={t.key}
              className={`navitem ${
                active && active.key === t.key ? 'active' : ''
              }`}
              onClick={() => setTab(t.key)}
            >
              {t.label}
            </button>
          ))}
          <div className="sidenav-foot">
            Demonstration environment — all data is synthetic.
          </div>
        </nav>

        <main className="content">
          {busy ? <div className="busy-bar" /> : null}
          {Component ? (
            <Component ctx={ctx} />
          ) : (
            <EmptyState title="No surfaces available for this role yet." />
          )}
        </main>
      </div>

      <Toast toast={toast} onDismiss={() => setToast(null)} />
    </div>
  );
}

function roleLabel(role) {
  return (
    {
      supplier: 'Supplier',
      reviewer: 'Reviewer',
      procurement_admin: 'Procurement',
      gov_observer: 'Government observer',
      funder: 'Funder',
    }[role] || role
  );
}

ReactDOM.createRoot(document.getElementById('supply-root')).render(<App />);
