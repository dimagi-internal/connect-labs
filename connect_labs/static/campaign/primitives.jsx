// primitives.jsx — UI primitives for the Campaign Utility Tool.
// Forked & extended from the CommCare HQ UI kit. Themeable via CSS vars (--accent etc).
const { useState, useEffect, useRef, createContext, useContext } = React;

const C = {
  purple: '#16006D',
  body: '#5F6A7D',
  border: '#DEE2E6',
  borderSoft: '#F1F3F5',
  surface: '#F8F9FA',
  muted: '#6C757D',
  faint: '#ADB5BD',
};

// status -> badge tone + label
const KYC_TONE = {
  approved: 'success',
  pending: 'warning',
  review: 'info',
  rejected: 'danger',
};
const KYC_LABEL = {
  approved: 'Approved',
  pending: 'Pending',
  review: 'In review',
  rejected: 'Rejected',
};
const PAY_TONE = {
  paid: 'success',
  approved: 'info',
  pending: 'warning',
  rejected: 'danger',
  hold: 'neutral',
};
const PAY_LABEL = {
  paid: 'Paid',
  approved: 'Approved',
  pending: 'Pending',
  rejected: 'Rejected',
  hold: 'On hold',
};

// ---------- BUTTON ----------
function Button({
  variant = 'primary',
  size = 'md',
  icon,
  iconRight,
  children,
  onClick,
  disabled,
  type = 'button',
  style = {},
  title,
}) {
  const variants = {
    primary: {
      bg: 'var(--accent)',
      color: '#fff',
      border: 'transparent',
      hover: 'var(--accent-dark)',
    },
    secondary: {
      bg: '#fff',
      color: C.purple,
      border: C.border,
      hover: C.surface,
    },
    ghost: {
      bg: 'transparent',
      color: 'var(--accent)',
      border: 'transparent',
      hover: 'var(--accent-soft)',
    },
    success: {
      bg: '#1E7B33',
      color: '#fff',
      border: 'transparent',
      hover: '#176127',
    },
    danger: {
      bg: '#E13019',
      color: '#fff',
      border: 'transparent',
      hover: '#B22312',
    },
    dangerSoft: {
      bg: '#FAD8D4',
      color: '#76190B',
      border: 'transparent',
      hover: '#F5C2BB',
    },
    link: {
      bg: 'transparent',
      color: 'var(--accent-dark)',
      border: 'transparent',
      hover: 'transparent',
    },
  };
  const v = variants[variant] || variants.primary;
  const sizes = {
    sm: { p: '5px 11px', f: 12.5, r: 7 },
    md: { p: '8px 16px', f: 13.5, r: 8 },
    lg: { p: '11px 22px', f: 15, r: 9 },
  };
  const s = sizes[size];
  const [hover, setHover] = useState(false);
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        background: disabled ? v.bg : hover ? v.hover : v.bg,
        color: v.color,
        border: `1px solid ${
          v.border === 'transparent' ? 'transparent' : v.border
        }`,
        padding: s.p,
        fontSize: s.f,
        borderRadius: s.r,
        fontFamily: 'inherit',
        fontWeight: 600,
        lineHeight: 1.2,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.45 : 1,
        display: 'inline-flex',
        alignItems: 'center',
        gap: 7,
        transition: 'background .15s, box-shadow .15s',
        textDecoration: variant === 'link' ? 'underline' : 'none',
        textUnderlineOffset: 2,
        whiteSpace: 'nowrap',
        ...style,
      }}
    >
      {icon && (
        <i className={`fa fa-${icon}`} style={{ fontSize: s.f - 1 }}></i>
      )}
      {children}
      {iconRight && (
        <i
          className={`fa fa-${iconRight}`}
          style={{ fontSize: s.f - 2, opacity: 0.8 }}
        ></i>
      )}
    </button>
  );
}

// ---------- CARD ----------
function Card({ children, padding = 22, style = {}, onClick, hoverable }) {
  const [h, setH] = useState(false);
  return (
    <div
      onClick={onClick}
      onMouseEnter={() => hoverable && setH(true)}
      onMouseLeave={() => hoverable && setH(false)}
      style={{
        background: '#fff',
        border: '1px solid ' + C.border,
        borderRadius: 12,
        boxShadow: h
          ? '0 4px 16px rgba(22,0,109,0.10)'
          : '0 1px 2px rgba(22,0,109,0.06)',
        padding,
        transition: 'box-shadow .15s, transform .15s',
        cursor: onClick ? 'pointer' : 'default',
        transform: h ? 'translateY(-1px)' : 'none',
        ...style,
      }}
    >
      {children}
    </div>
  );
}

// ---------- EYEBROW + SECTION TITLE ----------
function Eyebrow({ children, style = {} }) {
  return (
    <div
      style={{
        fontSize: 11,
        fontWeight: 600,
        color: 'var(--accent)',
        letterSpacing: '.12em',
        textTransform: 'uppercase',
        ...style,
      }}
    >
      {children}
    </div>
  );
}
function SectionTitle({ children, sub, style = {} }) {
  return (
    <div style={style}>
      <h2
        style={{
          margin: 0,
          fontSize: 20,
          color: C.purple,
          fontWeight: 600,
          letterSpacing: '-0.01em',
        }}
      >
        {children}
      </h2>
      {sub && (
        <p style={{ margin: '4px 0 0', color: C.body, fontSize: 13 }}>{sub}</p>
      )}
    </div>
  );
}

// ---------- FIELD / INPUTS ----------
function Field({ label, error, help, children, style = {} }) {
  return (
    <label style={{ display: 'block', marginBottom: 14, ...style }}>
      {label && (
        <div
          style={{
            fontSize: 12,
            fontWeight: 600,
            color: C.purple,
            marginBottom: 5,
          }}
        >
          {label}
        </div>
      )}
      {children}
      {error && (
        <div style={{ fontSize: 11, color: '#E13019', marginTop: 5 }}>
          {error}
        </div>
      )}
      {!error && help && (
        <div style={{ fontSize: 11, color: C.muted, marginTop: 5 }}>{help}</div>
      )}
    </label>
  );
}
const fieldBase = {
  fontFamily: 'inherit',
  fontSize: 13.5,
  padding: '9px 12px',
  border: '1px solid #CED4DA',
  borderRadius: 8,
  background: '#fff',
  color: C.purple,
  width: '100%',
  outline: 'none',
  boxSizing: 'border-box',
};
function TextInput(props) {
  const [f, setF] = useState(false);
  const { style, ...rest } = props;
  return (
    <input
      {...rest}
      onFocus={(e) => {
        setF(true);
        props.onFocus?.(e);
      }}
      onBlur={(e) => {
        setF(false);
        props.onBlur?.(e);
      }}
      style={{
        ...fieldBase,
        borderColor: f ? 'var(--accent)' : '#CED4DA',
        boxShadow: f ? '0 0 0 3px var(--accent-ring)' : 'none',
        ...style,
      }}
    />
  );
}
function Textarea(props) {
  const [f, setF] = useState(false);
  const { style, ...rest } = props;
  return (
    <textarea
      {...rest}
      onFocus={() => setF(true)}
      onBlur={() => setF(false)}
      style={{
        ...fieldBase,
        minHeight: 80,
        resize: 'vertical',
        borderColor: f ? 'var(--accent)' : '#CED4DA',
        boxShadow: f ? '0 0 0 3px var(--accent-ring)' : 'none',
        ...style,
      }}
    />
  );
}
function Select({ children, style = {}, ...props }) {
  return (
    <select
      {...props}
      style={{
        ...fieldBase,
        appearance: 'none',
        cursor: 'pointer',
        backgroundImage:
          "url(\"data:image/svg+xml,%3Csvg width='12' height='8' viewBox='0 0 12 8' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M1 1l5 5 5-5' stroke='%2316006D' stroke-width='2' fill='none'/%3E%3C/svg%3E\")",
        backgroundRepeat: 'no-repeat',
        backgroundPosition: 'right 12px center',
        paddingRight: 34,
        ...style,
      }}
    >
      {children}
    </select>
  );
}

// ---------- BADGE / DOT ----------
function Badge({ tone = 'neutral', children, style = {}, dot }) {
  const tones = {
    primary: { bg: 'var(--accent-soft)', fg: 'var(--accent-dark)' },
    info: { bg: '#CCECEE', fg: '#01545A' },
    success: { bg: '#D9ECD4', fg: '#1E4F12' },
    warning: { bg: '#FCEFCC', fg: '#7A5800' },
    danger: { bg: '#FAD8D4', fg: '#76190B' },
    neutral: { bg: '#E9ECEF', fg: '#495057' },
  };
  const t = tones[tone] || tones.neutral;
  return (
    <span
      style={{
        background: t.bg,
        color: t.fg,
        padding: '3px 10px',
        borderRadius: 999,
        fontSize: 11,
        fontWeight: 600,
        letterSpacing: '.02em',
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        ...style,
      }}
    >
      {dot && (
        <span
          style={{ width: 6, height: 6, borderRadius: 999, background: t.fg }}
        ></span>
      )}
      {children}
    </span>
  );
}
function KycBadge({ status }) {
  return (
    <Badge tone={KYC_TONE[status]} dot>
      {KYC_LABEL[status]}
    </Badge>
  );
}
function PayBadge({ status }) {
  return (
    <Badge tone={PAY_TONE[status]} dot>
      {PAY_LABEL[status]}
    </Badge>
  );
}

// ---------- STAT ----------
function Stat({ value, label, delta, deltaTone, icon, sub }) {
  return (
    <Card padding={20} style={{ height: '100%' }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
        }}
      >
        <div
          style={{
            fontSize: 11,
            color: C.muted,
            fontWeight: 600,
            letterSpacing: '.06em',
            textTransform: 'uppercase',
          }}
        >
          {label}
        </div>
        {icon && (
          <i
            className={`fa fa-${icon}`}
            style={{ color: 'var(--accent)', fontSize: 15, opacity: 0.8 }}
          ></i>
        )}
      </div>
      <div
        style={{
          fontSize: 32,
          fontWeight: 600,
          color: C.purple,
          letterSpacing: '-0.01em',
          marginTop: 6,
          lineHeight: 1.05,
        }}
      >
        {value}
      </div>
      <div
        style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8 }}
      >
        {delta && <Badge tone={deltaTone || 'success'}>{delta}</Badge>}
        {sub && <span style={{ fontSize: 12, color: C.muted }}>{sub}</span>}
      </div>
    </Card>
  );
}

// ---------- PROGRESS BAR ----------
function Progress({
  value,
  max = 100,
  color = 'var(--accent)',
  height = 8,
  track = C.borderSoft,
  label,
  right,
}) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div>
      {(label || right) && (
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            marginBottom: 6,
            fontSize: 12,
          }}
        >
          <span style={{ color: C.body, fontWeight: 500 }}>{label}</span>
          <span
            style={{
              color: C.purple,
              fontWeight: 600,
              fontFamily: 'ui-monospace, monospace',
            }}
          >
            {right}
          </span>
        </div>
      )}
      <div
        style={{
          background: track,
          height,
          borderRadius: 999,
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            background: color,
            height: '100%',
            width: pct + '%',
            borderRadius: 999,
            transition: 'width .4s cubic-bezier(.2,.7,.2,1)',
          }}
        ></div>
      </div>
    </div>
  );
}

// ---------- DONUT ----------
function Donut({
  segments,
  size = 132,
  thickness = 16,
  centerTop,
  centerBottom,
}) {
  const total = segments.reduce((a, s) => a + s.value, 0) || 1;
  const r = (size - thickness) / 2;
  const circ = 2 * Math.PI * r;
  let offset = 0;
  return (
    <div style={{ position: 'relative', width: size, height: size }}>
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        style={{ transform: 'rotate(-90deg)' }}
      >
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={C.borderSoft}
          strokeWidth={thickness}
        />
        {segments.map((s, i) => {
          const len = (s.value / total) * circ;
          const el = (
            <circle
              key={i}
              cx={size / 2}
              cy={size / 2}
              r={r}
              fill="none"
              stroke={s.color}
              strokeWidth={thickness}
              strokeDasharray={`${len} ${circ - len}`}
              strokeDashoffset={-offset}
              strokeLinecap="butt"
            />
          );
          offset += len;
          return el;
        })}
      </svg>
      <div
        style={{
          position: 'absolute',
          inset: 0,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <div
          style={{
            fontSize: 26,
            fontWeight: 600,
            color: C.purple,
            lineHeight: 1,
          }}
        >
          {centerTop}
        </div>
        {centerBottom && (
          <div style={{ fontSize: 11, color: C.muted, marginTop: 3 }}>
            {centerBottom}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------- TABS (pill) ----------
function PillTabs({ tabs, active, onChange, style = {} }) {
  return (
    <div
      style={{
        display: 'inline-flex',
        background: C.surface,
        border: '1px solid ' + C.border,
        borderRadius: 10,
        padding: 4,
        gap: 2,
        ...style,
      }}
    >
      {tabs.map((t) => (
        <button
          key={t.id}
          onClick={() => onChange(t.id)}
          style={{
            fontFamily: 'inherit',
            fontSize: 13,
            fontWeight: 600,
            padding: '7px 14px',
            borderRadius: 7,
            border: 'none',
            cursor: 'pointer',
            background: active === t.id ? '#fff' : 'transparent',
            color: active === t.id ? C.purple : C.muted,
            boxShadow:
              active === t.id ? '0 1px 3px rgba(22,0,109,.12)' : 'none',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 7,
            transition: 'all .12s',
          }}
        >
          {t.icon && (
            <i
              className={`fa fa-${t.icon}`}
              style={{
                fontSize: 12,
                color: active === t.id ? 'var(--accent)' : C.faint,
              }}
            ></i>
          )}
          {t.label}
          {t.count != null && (
            <span
              style={{
                fontSize: 11,
                background: active === t.id ? 'var(--accent-soft)' : '#E9ECEF',
                color: active === t.id ? 'var(--accent-dark)' : C.muted,
                padding: '1px 7px',
                borderRadius: 999,
              }}
            >
              {t.count}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}

// ---------- TABLE ----------
function Table({ columns, children, density = 'comfortable', style = {} }) {
  const pad = density === 'compact' ? '8px 14px' : '13px 16px';
  return (
    <div style={{ overflowX: 'auto', ...style }}>
      <table
        style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13.5 }}
      >
        <thead>
          <tr>
            {columns.map((c, i) => (
              <th
                key={i}
                style={{
                  textAlign: c.align || 'left',
                  padding: pad,
                  fontSize: 11,
                  fontWeight: 600,
                  color: C.muted,
                  letterSpacing: '.06em',
                  textTransform: 'uppercase',
                  borderBottom: '1px solid ' + C.border,
                  whiteSpace: 'nowrap',
                  width: c.width,
                  background: '#fff',
                  position: 'sticky',
                  top: 0,
                }}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody data-density={density}>{children}</tbody>
      </table>
    </div>
  );
}
function Row({ children, onClick, selected, style = {} }) {
  const [h, setH] = useState(false);
  return (
    <tr
      onClick={onClick}
      onMouseEnter={() => setH(true)}
      onMouseLeave={() => setH(false)}
      style={{
        background: selected
          ? 'var(--accent-soft)'
          : h && onClick
          ? C.surface
          : '#fff',
        cursor: onClick ? 'pointer' : 'default',
        transition: 'background .1s',
        ...style,
      }}
    >
      {children}
    </tr>
  );
}
function Cell({ children, align, mono, strong, style = {}, colSpan }) {
  const dens = 'comfortable';
  return (
    <td
      colSpan={colSpan}
      style={{
        padding: '13px 16px',
        borderBottom: '1px solid ' + C.borderSoft,
        textAlign: align || 'left',
        color: strong ? C.purple : C.body,
        fontWeight: strong ? 600 : 400,
        fontFamily: mono
          ? 'ui-monospace, SFMono-Regular, monospace'
          : 'inherit',
        verticalAlign: 'middle',
        ...style,
      }}
    >
      {children}
    </td>
  );
}

// ---------- AVATAR ----------
function Avatar({ name, size = 34, gender }) {
  const initials = name
    .split(' ')
    .map((p) => p[0])
    .slice(0, 2)
    .join('');
  const colors = [
    '#5D70D2',
    '#3843D0',
    '#9A5183',
    '#01A2A9',
    '#694AAA',
    '#B5651D',
  ];
  let h = 0;
  for (let i = 0; i < name.length; i++) h = name.charCodeAt(i) + ((h << 5) - h);
  const bg = colors[Math.abs(h) % colors.length];
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: '50%',
        background: bg,
        color: '#fff',
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: size * 0.38,
        fontWeight: 600,
        flexShrink: 0,
      }}
    >
      {initials}
    </div>
  );
}

// ---------- MODAL ----------
function Modal({ open, onClose, title, children, width = 540, footer }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);
  if (!open) return null;
  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(22,0,40,0.45)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
        padding: 24,
        animation: 'cutFade .15s ease-out',
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: '#fff',
          borderRadius: 14,
          width,
          maxWidth: '100%',
          maxHeight: '90vh',
          display: 'flex',
          flexDirection: 'column',
          boxShadow: '0 24px 60px rgba(22,0,60,.3)',
          animation: 'cutPop .18s cubic-bezier(.2,.7,.2,1)',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '18px 22px',
            borderBottom: '1px solid ' + C.border,
          }}
        >
          <h3
            style={{
              margin: 0,
              fontSize: 17,
              color: C.purple,
              fontWeight: 600,
            }}
          >
            {title}
          </h3>
          <button
            onClick={onClose}
            style={{
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              color: C.muted,
              fontSize: 18,
              lineHeight: 1,
              padding: 4,
            }}
          >
            <i className="fa fa-times"></i>
          </button>
        </div>
        <div style={{ padding: 22, overflowY: 'auto' }}>{children}</div>
        {footer && (
          <div
            style={{
              display: 'flex',
              justifyContent: 'flex-end',
              gap: 10,
              padding: '16px 22px',
              borderTop: '1px solid ' + C.border,
              background: C.surface,
              borderRadius: '0 0 14px 14px',
            }}
          >
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------- DRAWER (right side) ----------
function Drawer({ open, onClose, children, width = 760 }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);
  if (!open) return null;
  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(22,0,40,0.4)',
        zIndex: 900,
        display: 'flex',
        justifyContent: 'flex-end',
        animation: 'cutFade .15s ease-out',
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: '#fff',
          width,
          maxWidth: '94vw',
          height: '100%',
          boxShadow: '-12px 0 40px rgba(22,0,60,.2)',
          display: 'flex',
          flexDirection: 'column',
          animation: 'cutSlide .22s cubic-bezier(.2,.7,.2,1)',
        }}
      >
        {children}
      </div>
    </div>
  );
}

// ---------- TOAST ----------
const ToastCtx = createContext(null);
function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const push = (msg, tone = 'success') => {
    const id = Math.random();
    setToasts((t) => [...t, { id, msg, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3200);
  };
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div
        style={{
          position: 'fixed',
          bottom: 24,
          left: '50%',
          transform: 'translateX(-50%)',
          zIndex: 2000,
          display: 'flex',
          flexDirection: 'column',
          gap: 10,
          alignItems: 'center',
        }}
      >
        {toasts.map((t) => {
          const icon =
            t.tone === 'success'
              ? 'check-circle'
              : t.tone === 'danger'
              ? 'exclamation-circle'
              : 'info-circle';
          const col =
            t.tone === 'success'
              ? '#1E7B33'
              : t.tone === 'danger'
              ? '#E13019'
              : 'var(--accent)';
          return (
            <div
              key={t.id}
              style={{
                background: C.purple,
                color: '#fff',
                padding: '12px 18px',
                borderRadius: 10,
                fontSize: 13.5,
                fontWeight: 500,
                boxShadow: '0 10px 30px rgba(22,0,60,.35)',
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                animation: 'cutToast .25s cubic-bezier(.2,.7,.2,1)',
              }}
            >
              <i
                className={`fa fa-${icon}`}
                style={{
                  color: col === C.purple ? '#fff' : col,
                  background: col === C.purple ? 'none' : '#fff',
                  borderRadius: 999,
                }}
              ></i>
              {t.msg}
            </div>
          );
        })}
      </div>
    </ToastCtx.Provider>
  );
}
const useToast = () => useContext(ToastCtx);

// ---------- EMPTY ----------
function Empty({ icon = 'inbox', title, sub }) {
  return (
    <div style={{ textAlign: 'center', padding: '48px 24px', color: C.muted }}>
      <i
        className={`fa fa-${icon}`}
        style={{ fontSize: 30, color: C.faint, marginBottom: 12 }}
      ></i>
      <div style={{ fontSize: 15, fontWeight: 600, color: C.purple }}>
        {title}
      </div>
      {sub && <div style={{ fontSize: 13, marginTop: 4 }}>{sub}</div>}
    </div>
  );
}

// ---------- TOGGLE / CHECKBOX ----------
function Check({ checked, onChange, indeterminate }) {
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        onChange(!checked);
      }}
      style={{
        width: 18,
        height: 18,
        borderRadius: 5,
        border:
          '1.5px solid ' +
          (checked || indeterminate ? 'var(--accent)' : '#CED4DA'),
        background: checked || indeterminate ? 'var(--accent)' : '#fff',
        cursor: 'pointer',
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 0,
        flexShrink: 0,
      }}
    >
      {checked && (
        <i className="fa fa-check" style={{ color: '#fff', fontSize: 10 }}></i>
      )}
      {indeterminate && !checked && (
        <span style={{ width: 8, height: 2, background: '#fff' }}></span>
      )}
    </button>
  );
}

Object.assign(window, {
  CUTC: C,
  Button,
  Card,
  Eyebrow,
  SectionTitle,
  Field,
  TextInput,
  Textarea,
  Select,
  Badge,
  KycBadge,
  PayBadge,
  Stat,
  Progress,
  Donut,
  PillTabs,
  Table,
  Row,
  Cell,
  Avatar,
  Modal,
  Drawer,
  ToastProvider,
  useToast,
  Empty,
  Check,
  KYC_TONE,
  KYC_LABEL,
  PAY_TONE,
  PAY_LABEL,
});
