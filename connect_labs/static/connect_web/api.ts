/**
 * The player's client for labs' form engine API (connect_web/api.py). It only ever talks to labs:
 * labs signs the Formplayer call and adds the worker's HQ token, user, domain and app server-side.
 *
 * Requests are sent one at a time (a queue): Formplayer locks per user, so two at once would
 * only make the second wait or fail with "busy", and answers must arrive in the order typed.
 */

export interface PlayerConfig {
  opportunityId: string;
  opportunityName: string;
  appKind: 'learn' | 'deliver';
  appName: string;
  csrfToken: string;
  api: Record<string, string>;
  mediaMaxBytes: number;
}

/**
 * Where the player leaves to: the opportunity page, on the form's tab (`done` adds the
 * "submitted — waiting for Connect" flag). Built here from a fixed route rather than read from the
 * page, so no page text ever reaches `location.href` or an `href`. The route must match
 * `connect_web:opportunity` — test_views.py pins it.
 */
export const OPPORTUNITY_ROUTE = '/connect-web/opportunities/';

export function opportunityUrl(
  config: Pick<PlayerConfig, 'opportunityId' | 'appKind'>,
  done = false,
): string {
  const tab = config.appKind === 'deliver' ? 'deliver' : 'learn';
  const path = `${OPPORTUNITY_ROUTE}${encodeURIComponent(String(config.opportunityId || ''))}/`;
  return done ? `${path}?tab=${tab}&submitted=${tab}` : `${path}?tab=${tab}`;
}

export class ApiError extends Error {
  code: string;
  status: number;
  retryable: boolean;
  constructor(
    message: string,
    code: string,
    status: number,
    retryable = false,
  ) {
    super(message);
    this.code = code;
    this.status = status;
    this.retryable = retryable;
  }
  get isSessionGone() {
    return this.code === 'SESSION_EXPIRED' || this.code === 'SESSION_UNKNOWN';
  }
  get isNetwork() {
    return this.code === 'NETWORK';
  }
}

type Fetch = typeof fetch;

export function timezone(): {
  tz_offset_millis?: number;
  tz_from_browser?: string;
} {
  const out: { tz_offset_millis?: number; tz_from_browser?: string } = {};
  try {
    out.tz_offset_millis = -new Date().getTimezoneOffset() * 60000;
    const name = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (name) out.tz_from_browser = name;
  } catch {
    /* an old browser: Formplayer falls back to its default */
  }
  return out;
}

export async function readResponse(response: Response): Promise<any> {
  let body: any = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  if (response.ok) {
    if (body === null)
      throw new ApiError(
        "Labs sent an answer the player couldn't read.",
        'BAD_RESPONSE',
        response.status,
        true,
      );
    return body;
  }
  const err = body && body.error;
  if (err && typeof err.message === 'string') {
    throw new ApiError(
      err.message,
      err.code || 'ERROR',
      response.status,
      !!err.retryable,
    );
  }
  if (response.status === 403) {
    throw new ApiError(
      'Labs refused the request. Reload the page and try again.',
      'FORBIDDEN',
      403,
    );
  }
  throw new ApiError(
    `Labs had a problem (HTTP ${response.status}). Please try again.`,
    'HTTP_ERROR',
    response.status,
    response.status >= 500,
  );
}

export class EngineClient {
  private queue: Promise<unknown> = Promise.resolve();
  constructor(
    private config: PlayerConfig,
    private fetchImpl: Fetch = (input, init) => fetch(input, init),
  ) {}

  private enqueue<T>(run: () => Promise<T>): Promise<T> {
    const next = this.queue.then(run, run);
    this.queue = next.catch(() => undefined);
    return next;
  }

  private send(url: string, init: RequestInit): Promise<any> {
    return this.enqueue(async () => {
      let response: Response;
      try {
        response = await this.fetchImpl(url, {
          credentials: 'same-origin',
          ...init,
          headers: {
            'X-CSRFToken': this.config.csrfToken,
            Accept: 'application/json',
            ...(init.headers || {}),
          },
        });
      } catch {
        throw new ApiError(
          "Couldn't reach labs. Check your connection and try again.",
          'NETWORK',
          0,
          true,
        );
      }
      return readResponse(response);
    });
  }

  post(name: string, body: Record<string, unknown> = {}): Promise<any> {
    const url = this.config.api[name];
    if (!url)
      return Promise.reject(
        new ApiError(`Unknown action ${name}`, 'CLIENT', 0),
      );
    return this.send(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...timezone(), ...body }),
    });
  }

  navigate(selections: string[], extra: Record<string, unknown> = {}) {
    return this.post('navigate', { selections, ...extra });
  }
  details(selections: string[]) {
    return this.post('details', { selections });
  }
  answer(sessionId: string, ix: string, answer: unknown) {
    return this.post('answer', { session_id: sessionId, ix, answer });
  }
  newRepeat(sessionId: string, ix: string) {
    return this.post('new_repeat', { session_id: sessionId, ix });
  }
  deleteRepeat(sessionId: string, ix: string) {
    return this.post('delete_repeat', { session_id: sessionId, ix });
  }
  changeLocale(sessionId: string, locale: string) {
    return this.post('change_locale', { session_id: sessionId, locale });
  }
  submit(
    sessionId: string,
    answers: Record<string, unknown>,
    metaLocation: string | null,
  ) {
    return this.post('submit', {
      session_id: sessionId,
      answers,
      meta_location: metaLocation || undefined,
    });
  }
  sync() {
    return this.post('sync');
  }

  answerMedia(sessionId: string, ix: string, file: Blob, filename: string) {
    const form = new FormData();
    form.append('session_id', sessionId);
    form.append('ix', ix);
    const tz = timezone();
    if (tz.tz_offset_millis !== undefined)
      form.append('tz_offset_millis', String(tz.tz_offset_millis));
    if (tz.tz_from_browser) form.append('tz_from_browser', tz.tz_from_browser);
    form.append('file', file, filename);
    return this.send(this.config.api.answer_media, {
      method: 'POST',
      body: form,
    });
  }

  async progress(): Promise<any> {
    let response: Response;
    try {
      response = await this.fetchImpl(this.config.api.progress, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
    } catch {
      throw new ApiError("Couldn't reach labs.", 'NETWORK', 0, true);
    }
    return readResponse(response);
  }
}

/** Shrink a phone photo so it fits Formplayer's attachment limit: JPEG, longest side ≤ 1600px. */
export async function preparePhoto(
  file: File,
  maxBytes: number,
): Promise<{ blob: Blob; name: string }> {
  const isJpegOrPng = /^image\/(jpeg|png)$/.test(file.type);
  if (isJpegOrPng && file.size <= Math.min(maxBytes, 1_500_000)) {
    return {
      blob: file,
      name: file.type === 'image/png' ? 'photo.png' : 'photo.jpg',
    };
  }
  const bitmap = await loadImage(file);
  for (const [side, quality] of [
    [1600, 0.85],
    [1280, 0.8],
    [1024, 0.75],
    [800, 0.7],
  ] as const) {
    const scale = Math.min(1, side / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const ctx = canvas.getContext('2d');
    if (!ctx) break;
    ctx.drawImage(
      bitmap as CanvasImageSource,
      0,
      0,
      canvas.width,
      canvas.height,
    );
    const blob: Blob | null = await new Promise((resolve) =>
      canvas.toBlob(resolve, 'image/jpeg', quality),
    );
    if (blob && blob.size <= maxBytes) return { blob, name: 'photo.jpg' };
  }
  throw new ApiError(
    'This photo is too large to attach, even after shrinking it.',
    'PHOTO_TOO_LARGE',
    0,
  );
}

function loadImage(file: File): Promise<ImageBitmap | HTMLImageElement> {
  if (typeof createImageBitmap === 'function') {
    return createImageBitmap(file).catch(() => loadViaImg(file));
  }
  return loadViaImg(file);
}

function loadViaImg(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve(img);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(
        new ApiError(
          "That file couldn't be read as a photo.",
          'PHOTO_UNREADABLE',
          0,
        ),
      );
    };
    img.src = url;
  });
}
