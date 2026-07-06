import React from 'react';
import { createRoot } from 'react-dom/client';

import { resolveCard } from './pages-cards-helpers';

export interface CardMetric {
  label: string;
  value: string | number;
  trend?: string;
}

export interface CardPayload {
  title: string;
  card_type: string;
  status?: string;
  metrics?: CardMetric[];
  body?: string;
  cta?: { label: string; url: string } | null;
  render_code?: string | null;
  data?: Record<string, unknown>;
}

function Metrics({ metrics }: { metrics?: CardMetric[] }) {
  if (!metrics || metrics.length === 0) return null;
  return (
    <div className="pages-card__metrics">
      {metrics.map((m, i) => (
        <div className="pages-card__metric" key={i}>
          <div className="pages-card__metric-value">{m.value}</div>
          <div className="pages-card__metric-label">{m.label}</div>
        </div>
      ))}
    </div>
  );
}

function Cta({ cta }: { cta?: { label: string; url: string } | null }) {
  if (!cta) return null;
  return (
    <a className="pages-card__cta" href={cta.url}>
      {cta.label}
    </a>
  );
}

// Shipped renderers keyed by card_type. Unknown types fall through to Default.
interface WorkflowRun {
  id: number;
  created?: string;
  status?: string;
  open_url: string;
}

function StatusPill({ status }: { status?: string }) {
  const completed = status === 'completed';
  return (
    <span
      className={`pages-run__status pages-run__status--${
        completed ? 'completed' : 'in-progress'
      }`}
    >
      {completed ? 'Completed' : 'In progress'}
    </span>
  );
}

function WorkflowRuns({ payload }: { payload: CardPayload }) {
  const runs = (payload.data?.runs as WorkflowRun[]) || [];
  const total = (payload.data?.run_count as number) ?? runs.length;
  if (runs.length === 0) {
    return <div className="pages-card__empty">No runs yet.</div>;
  }
  return (
    <div className="pages-runs">
      <table className="pages-runs__table">
        <thead>
          <tr>
            <th>Run</th>
            <th>Created</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id}>
              <td className="pages-run__id">Run #{r.id}</td>
              <td className="pages-run__created">{r.created || '—'}</td>
              <td>
                <StatusPill status={r.status} />
              </td>
              <td className="pages-run__open">
                <a href={r.open_url}>Open →</a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {total > runs.length && (
        <div className="pages-runs__more">
          Showing {runs.length} of {total} runs
        </div>
      )}
    </div>
  );
}

const RENDERERS: Record<string, React.FC<{ payload: CardPayload }>> = {
  stat: ({ payload }) => (
    <>
      <Metrics metrics={payload.metrics} />
      <Cta cta={payload.cta} />
    </>
  ),
  audit_summary: ({ payload }) => (
    <>
      <Metrics metrics={payload.metrics} />
      <Cta cta={payload.cta} />
    </>
  ),
  list: ({ payload }) => (
    <>
      {payload.body && <div className="pages-card__body">{payload.body}</div>}
      <Cta cta={payload.cta} />
    </>
  ),
  summary: ({ payload }) => (
    <>
      {payload.body && <div className="pages-card__body">{payload.body}</div>}
      <Metrics metrics={payload.metrics} />
      <Cta cta={payload.cta} />
    </>
  ),
  workflow_runs: ({ payload }) => <WorkflowRuns payload={payload} />,
};

function DefaultRenderer({ payload }: { payload: CardPayload }) {
  return (
    <>
      {payload.body && <div className="pages-card__body">{payload.body}</div>}
      <Metrics metrics={payload.metrics} />
      <Cta cta={payload.cta} />
    </>
  );
}

// Babel escape hatch: transpile provider-supplied JSX into a component given `data`.
function RenderCode({ payload }: { payload: CardPayload }) {
  try {
    const wrapped = `(function(React, data){ ${payload.render_code} ; return Card; })`;
    // window.Babel is loaded by surface.html (Babel standalone, same as the workflow runner).
    const transformed = (window as any).Babel.transform(wrapped, {
      presets: ['react'],
    }).code;
    // eslint-disable-next-line no-eval
    const factory = eval(transformed);
    const Component = factory(React, payload.data || {});
    return <Component />;
  } catch (err) {
    return (
      <div className="pages-card__error">
        Card failed to render: {String(err)}
      </div>
    );
  }
}

export function CardBody({ payload }: { payload: CardPayload }) {
  const plan = resolveCard(payload);
  return (
    <div className="pages-card__inner">
      <div className="pages-card__title">{payload.title}</div>
      {plan.mode === 'render_code' ? (
        <RenderCode payload={payload} />
      ) : (
        React.createElement(RENDERERS[plan.cardType] || DefaultRenderer, {
          payload,
        })
      )}
    </div>
  );
}

function CardShell({ slug, index }: { slug: string; index: number }) {
  const [payload, setPayload] = React.useState<CardPayload | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    fetch(`/labs/p/${slug}/card/${index}/data/`)
      .then((r) =>
        r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)),
      )
      .then(setPayload)
      .catch((e) => setError(String(e)));
  }, [slug, index]);

  if (error) return <div className="pages-card pages-card--error">{error}</div>;
  if (!payload)
    return <div className="pages-card pages-card--loading">Loading…</div>;
  return <div className="pages-card">{<CardBody payload={payload} />}</div>;
}

export function mountSurface() {
  const root = document.getElementById('pages-root');
  if (!root) return;
  const slug = root.getAttribute('data-slug') || '';
  const indexes = JSON.parse(
    root.getAttribute('data-card-indexes') || '[]',
  ) as number[];
  createRoot(root).render(
    <div className="pages-grid">
      {indexes.map((index) => (
        <CardShell key={index} slug={slug} index={index} />
      ))}
    </div>,
  );
}

if (typeof document !== 'undefined') {
  document.addEventListener('DOMContentLoaded', mountSurface);
}
