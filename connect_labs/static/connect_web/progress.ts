/**
 * After a submission, the opportunity page waits for Connect: HQ has the form at once, but Connect
 * hears about it asynchronously (it reads HQ's forms), so learn progress, visits and payments move
 * a little later. Poll labs' progress endpoint a few times with backoff and reload when it moves.
 */

/** Labs' progress read. A poll asks for one kind (`?kind=`): the other is null. */
export interface Progress {
  claimed: boolean;
  learn: {
    modules_done: number;
    modules_total: number;
    assessments: number;
    passed: boolean;
    complete: boolean;
  } | null;
  deliver: { visits: number; statuses: Record<string, number> } | null;
}

/** Seconds to wait before each check: 3, 5, 8, 13, 20, 30, 45, 60 — about three minutes in all. */
export const POLL_DELAYS = [3, 5, 8, 13, 20, 30, 45, 60];

/** Did Connect record something new for this kind of form since `before`? */
export function progressed(
  before: Progress | null,
  after: Progress | null,
  kind: 'learn' | 'deliver',
): boolean {
  if (!before || !after) return false;
  if (kind === 'learn') {
    const [was, now] = [before.learn, after.learn];
    if (!was || !now) return false;
    return (
      now.modules_done > was.modules_done ||
      now.assessments > was.assessments ||
      (now.passed && !was.passed)
    );
  }
  if (!before.deliver || !after.deliver) return false;
  return after.deliver.visits > before.deliver.visits;
}

export const BASELINE_KEY = (opportunityId: string) =>
  `cw-progress-before:${opportunityId}`;

export function storeBaseline(opportunityId: string, progress: Progress) {
  try {
    sessionStorage.setItem(
      BASELINE_KEY(opportunityId),
      JSON.stringify(progress),
    );
  } catch {
    /* private mode: the page's own numbers are used instead */
  }
}

export function takeBaseline(opportunityId: string): Progress | null {
  try {
    const raw = sessionStorage.getItem(BASELINE_KEY(opportunityId));
    sessionStorage.removeItem(BASELINE_KEY(opportunityId));
    return raw ? (JSON.parse(raw) as Progress) : null;
  } catch {
    return null;
  }
}

interface PollOptions {
  url: string;
  kind: 'learn' | 'deliver';
  before: Progress;
  fetchImpl?: typeof fetch;
  sleep?: (ms: number) => Promise<void>;
  onCheck?: (attempt: number, total: number) => void;
}

/** Resolve "processed" as soon as progress moves, or "waiting" when the checks run out. */
export async function pollProgress(
  opts: PollOptions,
): Promise<'processed' | 'waiting'> {
  const sleep =
    opts.sleep || ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const doFetch =
    opts.fetchImpl || ((...a: Parameters<typeof fetch>) => fetch(...a));
  for (let i = 0; i < POLL_DELAYS.length; i++) {
    await sleep(POLL_DELAYS[i] * 1000);
    opts.onCheck?.(i + 1, POLL_DELAYS.length);
    try {
      const response = await doFetch(opts.url, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) continue;
      const now = (await response.json()) as Progress;
      if (progressed(opts.before, now, opts.kind)) return 'processed';
    } catch {
      /* offline for a moment: try again at the next step */
    }
  }
  return 'waiting';
}

/** Wire the "Submitted — waiting for Connect" banner on the opportunity page. */
export function mountProgress(banner: HTMLElement) {
  const url = banner.dataset.url || '';
  const kind = (banner.dataset.kind === 'deliver' ? 'deliver' : 'learn') as
    'learn' | 'deliver';
  const opportunityId = banner.dataset.opportunity || '';
  const pageBaseline = (() => {
    try {
      return JSON.parse(
        document.getElementById('cw-progress-baseline')?.textContent || 'null',
      );
    } catch {
      return null;
    }
  })();
  // Prefer what the player saw just before submitting: Connect may already have processed the
  // form by the time this page rendered.
  const before = takeBaseline(opportunityId) || pageBaseline;
  const text = banner.querySelector('[data-cw-progress-text]');
  const title = banner.querySelector('[data-cw-progress-label]');
  const icon = banner.querySelector('[data-cw-progress-icon]');
  const clean = () => {
    const u = new URL(window.location.href);
    u.searchParams.delete('submitted');
    return u.toString();
  };
  if (!before || !url) return;
  if (pageBaseline && progressed(before, pageBaseline, kind)) {
    if (title) title.textContent = 'Submitted — Connect has processed it';
    if (icon) icon.className = 'fa-solid fa-circle-check text-green-700';
    if (text) text.textContent = 'The progress below includes this form.';
    window.history.replaceState(null, '', clean());
    return;
  }
  pollProgress({
    url,
    kind,
    before,
    onCheck: (n, total) => {
      if (text)
        text.textContent = `CommCare HQ has the form. Checking Connect for it (${n} of ${total})…`;
    },
  }).then((result) => {
    if (result === 'processed') {
      if (title) title.textContent = 'Connect has processed it';
      if (icon) icon.className = 'fa-solid fa-circle-check text-green-700';
      if (text) text.textContent = 'Updating…';
      window.location.replace(clean());
    } else {
      if (icon) icon.className = 'fa-solid fa-clock text-sky-700';
      if (text)
        text.textContent =
          "Connect's numbers haven't changed. A form that isn't a learn module, an assessment or a paid visit (a registration, say) never changes them; otherwise it can take a few minutes, so reload later.";
    }
  });
}
