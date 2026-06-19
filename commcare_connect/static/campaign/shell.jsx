// shell.jsx — app shell: top brand bar + main tab navigation
const { useState: useStateShell } = React;

function TopBar({ role, onRole, campaign, onCampaign, scenario, user }) {
  const currentUser = user || { name: 'User', initials: 'U' };
  const [campOpen, setCampOpen] = useStateShell(false);
  const [roleOpen, setRoleOpen] = useStateShell(false);
  const roles = window.CUT_RBAC.ROLES.map((r) => r.name);
  const campaigns = [
    'Measles–Rubella Campaign · R2',
    'Polio SIA · Round 4',
    'Vitamin A + Deworming · 2026',
  ];
  return (
    <header
      style={{
        height: 58,
        background: '#fff',
        borderBottom: '1px solid ' + CUTC.border,
        display: 'flex',
        alignItems: 'center',
        padding: '0 22px',
        gap: 16,
        position: 'sticky',
        top: 0,
        zIndex: 50,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
        <img
          src={
            (window.__resources && window.__resources.logoPurple) ||
            'assets/dimagi-favicon-purple.png'
          }
          alt="Dimagi"
          style={{ height: 30 }}
        />
        <div style={{ lineHeight: 1.1 }}>
          <div
            style={{
              fontSize: 15.5,
              fontWeight: 600,
              color: CUTC.purple,
              letterSpacing: '-0.01em',
            }}
          >
            Campaign Utility
          </div>
          <div
            style={{
              fontSize: 10.5,
              color: CUTC.muted,
              letterSpacing: '.04em',
            }}
          >
            by Dimagi
          </div>
        </div>
      </div>
      <div style={{ height: 26, width: 1, background: CUTC.border }}></div>
      {/* campaign switcher */}
      <div style={{ position: 'relative' }}>
        <button
          onClick={() => {
            setCampOpen((o) => !o);
            setRoleOpen(false);
          }}
          style={{
            fontFamily: 'inherit',
            display: 'flex',
            alignItems: 'center',
            gap: 9,
            background: CUTC.surface,
            border: '1px solid ' + CUTC.border,
            padding: '7px 13px',
            borderRadius: 9,
            color: CUTC.purple,
            fontSize: 13,
            fontWeight: 600,
            cursor: 'pointer',
          }}
        >
          <span
            style={{
              width: 7,
              height: 7,
              borderRadius: 999,
              background: '#1E7B33',
            }}
          ></span>
          {campaign}
          <i
            className="fa fa-chevron-down"
            style={{ fontSize: 9, opacity: 0.5 }}
          ></i>
        </button>
        {campOpen && (
          <Dropdown
            items={campaigns}
            active={campaign}
            onPick={(c) => {
              onCampaign(c);
              setCampOpen(false);
            }}
            sub="Switch campaign"
          />
        )}
      </div>
      <div style={{ flex: 1 }}></div>
      <div style={{ position: 'relative', width: 230 }}>
        <input
          placeholder="Search workers, payments, activities…"
          style={{
            fontFamily: 'inherit',
            fontSize: 13,
            padding: '8px 12px 8px 32px',
            border: '1px solid ' + CUTC.border,
            borderRadius: 8,
            background: CUTC.surface,
            width: '100%',
            color: CUTC.purple,
            outline: 'none',
            boxSizing: 'border-box',
          }}
        />
        <i
          className="fa fa-search"
          style={{
            position: 'absolute',
            left: 12,
            top: '50%',
            transform: 'translateY(-50%)',
            fontSize: 12,
            color: CUTC.muted,
          }}
        ></i>
      </div>
      <button
        title="Sync status"
        style={{
          background: 'transparent',
          border: 'none',
          cursor: 'pointer',
          color: CUTC.muted,
          fontSize: 15,
          padding: 8,
          position: 'relative',
        }}
      >
        <i className="fa fa-sync-alt"></i>
      </button>
      <button
        title="Alerts"
        style={{
          background: 'transparent',
          border: 'none',
          cursor: 'pointer',
          color: CUTC.muted,
          fontSize: 16,
          padding: 8,
          position: 'relative',
        }}
      >
        <i className="fa fa-bell"></i>
        <span
          style={{
            position: 'absolute',
            top: 5,
            right: 5,
            width: 7,
            height: 7,
            borderRadius: 999,
            background: '#E13019',
            border: '1.5px solid #fff',
          }}
        ></span>
      </button>
      {/* role switcher */}
      <div style={{ position: 'relative' }}>
        <button
          onClick={() => {
            setRoleOpen((o) => !o);
            setCampOpen(false);
          }}
          style={{
            fontFamily: 'inherit',
            display: 'flex',
            alignItems: 'center',
            gap: 9,
            background: 'transparent',
            border: 'none',
            cursor: 'pointer',
            padding: 0,
          }}
        >
          <div
            style={{
              width: 34,
              height: 34,
              borderRadius: '50%',
              background: 'var(--accent)',
              color: '#fff',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 13,
              fontWeight: 600,
            }}
          >
            {currentUser.initials}
          </div>
          <div style={{ textAlign: 'left', lineHeight: 1.15 }}>
            <div
              style={{ fontSize: 12.5, fontWeight: 600, color: CUTC.purple }}
            >
              {currentUser.name}
            </div>
            <div style={{ fontSize: 11, color: CUTC.muted }}>{role}</div>
          </div>
          <i
            className="fa fa-chevron-down"
            style={{ fontSize: 9, opacity: 0.5, color: CUTC.muted }}
          ></i>
        </button>
        {roleOpen && (
          <Dropdown
            items={roles}
            active={role}
            onPick={(r) => {
              onRole(r);
              setRoleOpen(false);
            }}
            sub="View as role (RBAC)"
          />
        )}
      </div>
    </header>
  );
}

function Dropdown({ items, active, onPick, sub }) {
  return (
    <div
      style={{
        position: 'absolute',
        top: 'calc(100% + 8px)',
        right: 0,
        background: '#fff',
        border: '1px solid ' + CUTC.border,
        borderRadius: 10,
        boxShadow: '0 12px 32px rgba(22,0,60,.16)',
        padding: 6,
        minWidth: 230,
        zIndex: 100,
      }}
    >
      {sub && (
        <div
          style={{
            fontSize: 10,
            fontWeight: 600,
            color: CUTC.faint,
            letterSpacing: '.1em',
            textTransform: 'uppercase',
            padding: '6px 10px 4px',
          }}
        >
          {sub}
        </div>
      )}
      {items.map((it) => (
        <button
          key={it}
          onClick={() => onPick(it)}
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            width: '100%',
            textAlign: 'left',
            fontFamily: 'inherit',
            padding: '8px 10px',
            background: 'transparent',
            border: 'none',
            borderRadius: 7,
            cursor: 'pointer',
            fontSize: 13,
            fontWeight: it === active ? 600 : 500,
            color: it === active ? CUTC.purple : CUTC.body,
          }}
          onMouseEnter={(e) =>
            (e.currentTarget.style.background = CUTC.surface)
          }
          onMouseLeave={(e) =>
            (e.currentTarget.style.background = 'transparent')
          }
        >
          {it}
          {it === active && (
            <i
              className="fa fa-check"
              style={{ color: 'var(--accent)', fontSize: 12 }}
            ></i>
          )}
        </button>
      ))}
    </div>
  );
}

function Sidebar({ active, onChange, subs, onSub, showAdmin }) {
  const items = [
    { id: 'overview', label: 'Overview', icon: 'gauge-high' },
    {
      id: 'workers',
      label: 'Workers',
      icon: 'users',
      subs: [
        { id: 'payments', label: 'Worker Payments', icon: 'money-bill-wave' },
        { id: 'kyc', label: 'Worker KYC', icon: 'id-card' },
        { id: 'profile', label: 'Worker Profiles', icon: 'address-card' },
      ],
    },
    {
      id: 'activity',
      label: 'Activity',
      icon: 'list-check',
      subs: [
        { id: 'details', label: 'Activity Details', icon: 'clipboard-list' },
        {
          id: 'planning',
          label: 'Microplanning & Budget',
          icon: 'map-location-dot',
        },
      ],
    },
    { id: 'reporting', label: 'Reporting & Monitoring', icon: 'chart-line' },
    { section: 'Administration', adminOnly: true },
    {
      id: 'sysadmin',
      label: 'System Administration',
      icon: 'user-shield',
      adminOnly: true,
      subs: [
        { id: 'users', label: 'User Management', icon: 'users-gear' },
        { id: 'connections', label: 'Connection Settings', icon: 'plug' },
      ],
    },
    { section: 'Resources' },
    {
      id: 'training',
      label: 'Training Hub',
      icon: 'graduation-cap',
      badge: 'Public',
    },
  ];
  const Row = ({ on, icon, label, onClick, indent, badge }) => (
    <button
      onClick={onClick}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 11,
        width: '100%',
        textAlign: 'left',
        fontFamily: 'inherit',
        cursor: 'pointer',
        padding: indent ? '8px 12px 8px 40px' : '10px 12px',
        background: on ? 'var(--accent-soft)' : 'transparent',
        border: 'none',
        borderRadius: 9,
        color: on ? CUTC.purple : CUTC.body,
        fontWeight: on ? 600 : 500,
        fontSize: indent ? 13 : 13.5,
        transition: 'background .12s',
      }}
      onMouseEnter={(e) => {
        if (!on) e.currentTarget.style.background = CUTC.surface;
      }}
      onMouseLeave={(e) => {
        if (!on) e.currentTarget.style.background = 'transparent';
      }}
    >
      <i
        className={`fa fa-${icon}`}
        style={{
          width: 16,
          fontSize: indent ? 11 : 13,
          color: on ? 'var(--accent)' : CUTC.faint,
          textAlign: 'center',
        }}
      ></i>
      <span style={{ flex: 1 }}>{label}</span>
      {badge && (
        <span
          style={{
            fontSize: 9.5,
            fontWeight: 700,
            letterSpacing: '.06em',
            textTransform: 'uppercase',
            color: '#1E7B33',
            background: '#D9ECD4',
            padding: '2px 6px',
            borderRadius: 999,
          }}
        >
          {badge}
        </span>
      )}
    </button>
  );
  return (
    <aside
      style={{
        width: 240,
        flexShrink: 0,
        background: '#fff',
        borderRight: '1px solid ' + CUTC.border,
        padding: '16px 12px',
        position: 'sticky',
        top: 58,
        height: 'calc(100vh - 58px)',
        overflowY: 'auto',
        display: 'flex',
        flexDirection: 'column',
        gap: 2,
      }}
    >
      <div
        style={{
          fontSize: 10,
          fontWeight: 600,
          color: CUTC.faint,
          letterSpacing: '.12em',
          textTransform: 'uppercase',
          padding: '4px 12px 8px',
        }}
      >
        Campaign
      </div>
      {items.map((it, idx) => {
        if (it.adminOnly && !showAdmin) return null;
        if (it.section)
          return (
            <div
              key={'sec' + idx}
              style={{
                fontSize: 10,
                fontWeight: 600,
                color: CUTC.faint,
                letterSpacing: '.12em',
                textTransform: 'uppercase',
                padding: '14px 12px 8px',
              }}
            >
              {it.section}
            </div>
          );
        const on = active === it.id;
        return (
          <React.Fragment key={it.id}>
            <Row
              on={on}
              icon={it.icon}
              label={it.label}
              badge={it.badge}
              onClick={() => {
                onChange(it.id);
                window.scrollTo(0, 0);
              }}
            />
            {it.subs && on && (
              <div
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 2,
                  margin: '2px 0 4px',
                }}
              >
                {it.subs.map((s) => (
                  <Row
                    key={s.id}
                    on={subs[it.id] === s.id}
                    icon={s.icon}
                    label={s.label}
                    indent
                    onClick={() => {
                      onSub(it.id, s.id);
                      window.scrollTo(0, 0);
                    }}
                  />
                ))}
              </div>
            )}
          </React.Fragment>
        );
      })}
      <div style={{ flex: 1 }}></div>
      <div
        style={{ height: 1, background: CUTC.border, margin: '8px 12px' }}
      ></div>
      <Row icon="circle-question" label="Help & docs" onClick={() => {}} />
      <div
        style={{
          padding: '10px 12px 2px',
          fontSize: 11,
          color: CUTC.faint,
          display: 'flex',
          alignItems: 'center',
          gap: 7,
        }}
      >
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: 999,
            background: '#1E7B33',
          }}
        ></span>
        Synced · 38 min ago
      </div>
    </aside>
  );
}

// page wrapper
function Page({ children, max = 1320 }) {
  return (
    <div style={{ padding: '28px 32px 64px', maxWidth: max, margin: '0 auto' }}>
      {children}
    </div>
  );
}
function PageHead({ eyebrow, title, sub, actions }) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'flex-end',
        marginBottom: 24,
        gap: 20,
        flexWrap: 'wrap',
      }}
    >
      <div>
        {eyebrow && <Eyebrow style={{ marginBottom: 6 }}>{eyebrow}</Eyebrow>}
        <h1
          style={{
            margin: 0,
            fontSize: 28,
            color: CUTC.purple,
            fontWeight: 600,
            letterSpacing: '-0.02em',
          }}
        >
          {title}
        </h1>
        {sub && (
          <p
            style={{
              margin: '6px 0 0',
              color: CUTC.body,
              fontSize: 14,
              maxWidth: 720,
            }}
          >
            {sub}
          </p>
        )}
      </div>
      {actions && (
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          {actions}
        </div>
      )}
    </div>
  );
}

Object.assign(window, { TopBar, Sidebar, Page, PageHead });
