// app.jsx — root: state, navigation, theming.
// Plan 1 (foundation): renders the verbatim shell (TopBar + Sidebar) with
// placeholder tab bodies. Later plans replace TabPlaceholder with the real tab
// modules and introduce data-api.js. The dev-only "tweaks" panel from the
// prototype is intentionally dropped; its seeded config becomes CONFIG below.
const { useState: useStateApp, useEffect: useEffectApp } = React;

const BOOTSTRAP = (() => {
  try {
    return JSON.parse(
      document.getElementById('campaign-bootstrap').textContent,
    );
  } catch (e) {
    return {
      user: { name: 'User', role: 'campaign_admin' },
      campaign: { name: 'Campaign' },
    };
  }
})();

// Data-layer fallback for first paint; data-api.js will populate window.CUT_DATA in a later plan.
const CUT_DATA = window.CUT_DATA || { campaign: BOOTSTRAP.campaign };

// Initials for the sidebar avatar: first letter of the first two words, else
// the first two characters. Falls back to '?' for an empty/missing name.
const initialsOf = (name) => {
  const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
};

const CURRENT_USER = {
  name: BOOTSTRAP.user.name,
  initials: initialsOf(BOOTSTRAP.user.name),
};

// Server role keys (rbac.py) -> the display names window.CUT_RBAC (perms.js) expects.
const ROLE_DISPLAY = {
  campaign_admin: 'Campaign Administrator',
  payment_admin: 'Payment Administrator',
  compliance_admin: 'Compliance Administrator',
  operations_manager: 'Operations Manager',
  reporting_user: 'Reporting User',
};

const ACCENTS = {
  '#5D70D2': { dark: '#3F50A8', soft: '#E5E8FA', ring: 'rgba(93,112,210,.28)' }, // CommCare blue
  '#3843D0': { dark: '#2A33A6', soft: '#E2E4FB', ring: 'rgba(56,67,208,.26)' }, // Connect indigo
  '#16006D': { dark: '#0E0047', soft: '#E4E0F2', ring: 'rgba(22,0,109,.22)' }, // Deep purple
  '#01A2A9': { dark: '#017F84', soft: '#D2EEEF', ring: 'rgba(1,162,169,.26)' }, // Teal
};

// Prototype tweak defaults, now fixed app config.
const CONFIG = {
  accent: '#5D70D2',
  density: 'comfortable',
  scenario: 'current',
  showAlerts: true,
};

function applyAccent(hex) {
  const a = ACCENTS[hex] || ACCENTS['#5D70D2'];
  const r = document.documentElement.style;
  r.setProperty('--accent', hex);
  r.setProperty('--accent-dark', a.dark);
  r.setProperty('--accent-soft', a.soft);
  r.setProperty('--accent-ring', a.ring);
}

function TabPlaceholder({ name }) {
  return (
    <Page>
      <div style={{ padding: 48, textAlign: 'center', color: CUTC.muted }}>
        <i
          className="fa fa-screwdriver-wrench"
          style={{
            fontSize: 28,
            marginBottom: 12,
            display: 'block',
            color: CUTC.faint,
          }}
        ></i>
        <div style={{ fontWeight: 600, color: CUTC.purple, marginBottom: 4 }}>
          {name}
        </div>
        <div style={{ fontSize: 13 }}>Coming soon.</div>
      </div>
    </Page>
  );
}

const TAB_LABELS = {
  overview: 'Overview',
  'workers:payments': 'Worker Payments',
  'workers:kyc': 'Worker KYC',
  'workers:profile': 'Worker Profiles',
  'activity:details': 'Activity Details',
  'activity:planning': 'Microplanning & Budget',
  reporting: 'Reporting & Monitoring',
  'sysadmin:users': 'User Management',
  'sysadmin:connections': 'Connection Settings',
  training: 'Training Hub',
};

function App() {
  const [tab, setTab] = useStateApp('overview');
  const [wSub, setWSub] = useStateApp('payments');
  const [aSub, setASub] = useStateApp('details');
  const [admSub, setAdmSub] = useStateApp('users');
  const [role, setRole] = useStateApp(
    ROLE_DISPLAY[BOOTSTRAP.user.role] || 'Campaign Administrator',
  );
  const [campaign, setCampaign] = useStateApp(
    (CUT_DATA.campaign && CUT_DATA.campaign.name) || 'Campaign',
  );

  useEffectApp(() => {
    applyAccent(CONFIG.accent);
  }, []);

  const RBAC = window.CUT_RBAC;
  const showAdmin =
    RBAC.can(role, 'users', 'view') || RBAC.can(role, 'connections', 'view');
  const jump = (toTab, sub) => {
    setTab(toTab);
    if (sub) setWSub(sub);
    window.scrollTo(0, 0);
  };

  // If a non-admin role is active while on an admin tab, bounce to overview.
  useEffectApp(() => {
    if (tab === 'sysadmin' && !showAdmin) setTab('overview');
  }, [role]);

  const subs = { workers: wSub, activity: aSub, sysadmin: admSub };
  const onSub = (tabId, subId) => {
    setTab(tabId);
    if (tabId === 'workers') setWSub(subId);
    else if (tabId === 'activity') setASub(subId);
    else if (tabId === 'sysadmin') setAdmSub(subId);
  };

  let activeName = TAB_LABELS[tab] || 'Overview';
  if (tab === 'workers') activeName = TAB_LABELS['workers:' + wSub];
  else if (tab === 'activity') activeName = TAB_LABELS['activity:' + aSub];
  else if (tab === 'sysadmin') activeName = TAB_LABELS['sysadmin:' + admSub];

  return (
    <div style={{ minHeight: '100vh', background: CUTC.surface }}>
      <TopBar
        role={role}
        onRole={setRole}
        campaign={campaign}
        onCampaign={setCampaign}
        scenario={CONFIG.scenario}
        user={CURRENT_USER}
      />
      <div style={{ display: 'flex', alignItems: 'flex-start' }}>
        <Sidebar
          active={tab}
          onChange={setTab}
          subs={subs}
          onSub={onSub}
          showAdmin={showAdmin}
        />
        <main style={{ flex: 1, minWidth: 0 }}>
          {tab === 'overview' ? (
            <OverviewTab
              scenario={CONFIG.scenario}
              showAlerts={CONFIG.showAlerts}
              onJump={jump}
            />
          ) : tab === 'workers' ? (
            <WorkersTab
              sub={wSub}
              onSub={setWSub}
              density={CONFIG.density}
              role={role}
            />
          ) : tab === 'activity' && aSub === 'details' ? (
            <ActivityDetails density={CONFIG.density} role={role} />
          ) : tab === 'activity' && aSub === 'planning' ? (
            <PlanningTab density={CONFIG.density} role={role} />
          ) : tab === 'reporting' ? (
            <ReportingTab density={CONFIG.density} />
          ) : tab === 'sysadmin' && admSub === 'users' ? (
            <UserManagement density={CONFIG.density} role={role} />
          ) : tab === 'sysadmin' && admSub === 'connections' ? (
            <ConnectionSettings density={CONFIG.density} />
          ) : tab === 'training' ? (
            <TrainingHub role={role} />
          ) : (
            <TabPlaceholder name={activeName} />
          )}
        </main>
      </div>
    </div>
  );
}

function mount() {
  ReactDOM.createRoot(document.getElementById('root')).render(
    React.createElement(ToastProvider, null, React.createElement(App)),
  );
}
if (window.campaignLoadData) {
  window
    .campaignLoadData()
    .then(mount)
    .catch(function (e) {
      document.getElementById('root').innerHTML =
        '<div style="padding:48px;text-align:center;color:#E13019;font-family:sans-serif">Could not load campaign data. Please refresh.</div>';
      console.error(e);
    });
} else {
  mount();
}
