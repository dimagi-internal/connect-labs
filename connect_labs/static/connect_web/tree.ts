/**
 * Formplayer's question tree, read the way commcare-core writes it.
 *
 * Shapes: Formplayer api/json/PromptToJson.java + Walker.java, round-tripped through
 * beans/QuestionBean.java (see connect_web/tests/fixtures/formplayer_responses.json `_sources`).
 * The essentials:
 *   - `type` is "question" | "sub-group" | "repeat-juncture".
 *   - Irrelevant nodes are simply ABSENT from the tree (`relevant` is always 0 in the bean), so
 *     everything present is shown.
 *   - `datatype` is str | int | longint | float | date | time | datetime | select | multiselect |
 *     geo | barcode | binary | info; `control` is javarosa's CONTROL_* number.
 *   - Select answers are 1-based ordinals; multiselect a list of them; date "yyyy-MM-dd";
 *     time "HH:mm"; datetime "yyyy-MM-ddTHH:mm:ss+ZZ:ZZ"; geo [lat, lon].
 *   - A repeat instance is a sub-group with repeatable "true", exists "true" (delete true when
 *     it may be removed); the "add another" placeholder has exists "false" and "add-choice".
 *
 * Everything here is pure, so it is unit-tested on fixture trees (tree.test.js).
 */

export type Answer = null | string | number | Array<number | string>;

export interface FormNode {
  type: 'question' | 'sub-group' | 'repeat-juncture' | string;
  ix: string;
  caption?: string | null;
  caption_markdown?: string | null;
  datatype?: string | null;
  control?: number | null;
  answer?: Answer;
  choices?: string[] | null;
  required?: number | boolean | null;
  style?: { raw?: string | null } | null;
  help?: string | null;
  hint?: string | null;
  repeatable?: string | boolean | null;
  exists?: string | boolean | null;
  delete?: boolean | null;
  'add-choice'?: string | null;
  header?: string | null;
  children?: FormNode[] | null;
}

// javarosa org.javarosa.core.model.Constants
export const CONTROL = {
  INPUT: 1,
  SELECT_ONE: 2,
  SELECT_MULTI: 3,
  TRIGGER: 9,
  IMAGE_CHOOSE: 10,
  LABEL: 11,
  AUDIO_CAPTURE: 12,
  VIDEO_CAPTURE: 13,
  DOCUMENT_UPLOAD: 14,
};

export type Widget =
  | 'label'
  | 'text'
  | 'phone'
  | 'int'
  | 'longint'
  | 'decimal'
  | 'date'
  | 'time'
  | 'datetime'
  | 'select'
  | 'dropdown'
  | 'combobox'
  | 'multiselect'
  | 'geo'
  | 'image'
  | 'signature'
  | 'audio'
  | 'video'
  | 'document'
  | 'unsupported';

/** The appearance tokens of a node (`style.raw`, space-separated). */
export function styles(node: FormNode): string[] {
  const raw = node?.style?.raw;
  return raw ? String(raw).split(/\s+/).filter(Boolean) : [];
}

export function hasStyle(node: FormNode, token: string): boolean {
  return styles(node).includes(token);
}

const truthy = (v: unknown) => v === true || v === 'true';
const falsy = (v: unknown) => v === false || v === 'false';

export const isQuestion = (n: FormNode) => n.type === 'question';
export const isGroup = (n: FormNode) =>
  n.type === 'sub-group' && !truthy(n.repeatable);
export const isRepeatInstance = (n: FormNode) =>
  n.type === 'sub-group' && truthy(n.repeatable) && !falsy(n.exists);
export const isRepeatPlaceholder = (n: FormNode) =>
  n.type === 'sub-group' && truthy(n.repeatable) && falsy(n.exists);
export const isJuncture = (n: FormNode) => n.type === 'repeat-juncture';
export const isRequired = (n: FormNode) =>
  n.required === 1 || n.required === true;
export const isFieldList = (n: FormNode) => hasStyle(n, 'field-list');

/** Which input a question gets. Mirrors HQ Web Apps' entries.getEntry, mobile-first. */
export function widgetFor(q: FormNode): Widget {
  switch (q.datatype) {
    case 'info':
      return 'label';
    case 'str':
    case 'barcode':
      return hasStyle(q, 'numeric') ? 'phone' : 'text';
    case 'int':
      return 'int';
    case 'longint':
      return 'longint';
    case 'float':
      return 'decimal';
    case 'date':
      return 'date';
    case 'time':
      return 'time';
    case 'datetime':
      return 'datetime';
    case 'select':
      if (hasStyle(q, 'combobox')) return 'combobox';
      if (hasStyle(q, 'minimal')) return 'dropdown';
      // Very long lists are easier to search than to scroll on a phone.
      return (q.choices?.length ?? 0) > 30 ? 'combobox' : 'select';
    case 'multiselect':
      return 'multiselect';
    case 'geo':
      return 'geo';
    case 'binary':
      switch (q.control) {
        case CONTROL.IMAGE_CHOOSE:
          return hasStyle(q, 'signature') ? 'signature' : 'image';
        case CONTROL.AUDIO_CAPTURE:
          return 'audio';
        case CONTROL.VIDEO_CAPTURE:
          return 'video';
        case CONTROL.DOCUMENT_UPLOAD:
          return 'document';
        default:
          return 'unsupported';
      }
    default:
      return 'unsupported';
  }
}

/** Widgets the web can't capture yet: shown as such, never silently skipped. */
export const UNSUPPORTED_WIDGETS: Widget[] = [
  'signature',
  'audio',
  'video',
  'document',
  'unsupported',
];

// ---------------------------------------------------------------------------
// Form indexes
// ---------------------------------------------------------------------------

/** "3_1,2" → [[3, 1], [2, -1]]: (local index, instance) per level. */
export function parseIx(ix: string): Array<[number, number]> {
  if (ix === '<') return [[-1, -1]];
  if (ix === '>') return [[Number.MAX_SAFE_INTEGER, -1]];
  return String(ix)
    .split(',')
    .map((step) => {
      const [i, m] = step.trim().replace(/J$/, '').split(/[_.:]/);
      return [Number(i), m === undefined ? -1 : Number(m)] as [number, number];
    });
}

/** Document order of two form indexes. */
export function compareIx(a: string, b: string): number {
  const pa = parseIx(a);
  const pb = parseIx(b);
  for (let k = 0; k < Math.max(pa.length, pb.length); k++) {
    if (pa[k] === undefined) return -1;
    if (pb[k] === undefined) return 1;
    if (pa[k][0] !== pb[k][0]) return pa[k][0] - pb[k][0];
    if (pa[k][1] !== pb[k][1]) return pa[k][1] - pb[k][1];
  }
  return 0;
}

/** The repeat a sub-group belongs to: its index without the last instance number. */
export function repeatKey(ix: string): string {
  const steps = String(ix).split(',');
  const last = steps.pop() || '';
  return [...steps, last.split(/[_.:]/)[0]].join(',');
}

// ---------------------------------------------------------------------------
// Walking the tree
// ---------------------------------------------------------------------------

export function walk(
  nodes: FormNode[] | null | undefined,
  visit: (n: FormNode) => void,
): void {
  for (const n of nodes || []) {
    visit(n);
    if (n.children?.length) walk(n.children, visit);
  }
}

export function questions(nodes: FormNode[] | null | undefined): FormNode[] {
  const out: FormNode[] = [];
  walk(nodes, (n) => {
    if (isQuestion(n)) out.push(n);
  });
  return out;
}

export function findByIx(
  nodes: FormNode[] | null | undefined,
  ix: string,
): FormNode | null {
  let found: FormNode | null = null;
  walk(nodes, (n) => {
    if (!found && n.ix === ix) found = n;
  });
  return found;
}

/** Every answer, as submit-all takes it (HQ's accumulateAnswers): info → "OK". */
export function collectAnswers(tree: FormNode[]): Record<string, Answer> {
  const answers: Record<string, Answer> = {};
  for (const q of questions(tree)) {
    answers[q.ix] = q.datatype === 'info' ? 'OK' : (q.answer ?? null);
  }
  return answers;
}

export function isEmptyAnswer(answer: Answer | undefined): boolean {
  return (
    answer === null ||
    answer === undefined ||
    answer === '' ||
    (Array.isArray(answer) && answer.length === 0)
  );
}

/** The required questions under `nodes` with no answer: {ix: message}. */
export function missingRequired(nodes: FormNode[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const q of questions(nodes)) {
    if (q.datatype !== 'info' && isRequired(q) && isEmptyAnswer(q.answer))
      out[q.ix] = 'An answer is required.';
  }
  return out;
}

// ---------------------------------------------------------------------------
// Screens ("pages")
// ---------------------------------------------------------------------------

/**
 * One screen of the form.
 *   - A question outside any field-list group is a screen of its own, as on the phone.
 *   - A field-list group is one screen with all its questions.
 *   - A plain group is not a screen: its questions are, each titled with the group's caption.
 *   - A repeat (its instances and its "add another" placeholder) is one screen.
 */
export interface Page {
  key: string;
  kind: 'question' | 'group' | 'repeat';
  context: string[]; // captions of the enclosing plain groups
  nodes: FormNode[];
}

export function buildPages(
  tree: FormNode[] | null | undefined,
  context: string[] = [],
): Page[] {
  const pages: Page[] = [];
  const nodes = tree || [];
  for (let i = 0; i < nodes.length; i++) {
    const n = nodes[i];
    if (isQuestion(n)) {
      pages.push({ key: n.ix, kind: 'question', context, nodes: [n] });
    } else if (isRepeatInstance(n) || isRepeatPlaceholder(n) || isJuncture(n)) {
      // Gather this repeat's consecutive instances and its placeholder into one screen.
      const key = isJuncture(n) ? n.ix : repeatKey(n.ix);
      const group: FormNode[] = [n];
      while (
        i + 1 < nodes.length &&
        !isJuncture(n) &&
        (isRepeatInstance(nodes[i + 1]) || isRepeatPlaceholder(nodes[i + 1])) &&
        repeatKey(nodes[i + 1].ix) === key
      ) {
        group.push(nodes[++i]);
      }
      pages.push({
        key: `repeat:${key}`,
        kind: 'repeat',
        context,
        nodes: group,
      });
    } else if (isGroup(n)) {
      if (!n.children?.length) continue;
      if (isFieldList(n)) {
        pages.push({ key: n.ix, kind: 'group', context, nodes: [n] });
      } else {
        const caption = (n.caption || '').trim();
        pages.push(
          ...buildPages(n.children, caption ? [...context, caption] : context),
        );
      }
    }
  }
  return pages;
}

/** The first index a page covers, for keeping one's place when the tree changes. */
export function pageAnchor(page: Page): string {
  return page.key.startsWith('repeat:')
    ? page.key.slice('repeat:'.length)
    : page.key;
}

/**
 * Where to be after the tree changed: the same page if it still exists, else the first page at
 * or after where we were (a question that became irrelevant has gone), else the last page.
 */
export function relocate(pages: Page[], key: string | null): number {
  if (!pages.length) return 0;
  if (key == null) return 0;
  const exact = pages.findIndex((p) => p.key === key);
  if (exact >= 0) return exact;
  const anchor = key.startsWith('repeat:') ? key.slice('repeat:'.length) : key;
  const after = pages.findIndex((p) => compareIx(pageAnchor(p), anchor) >= 0);
  return after >= 0 ? after : pages.length - 1;
}

/** The page holding a question (to jump to a submission error). */
export function pageOf(pages: Page[], ix: string): number {
  return pages.findIndex(
    (p) =>
      questions(p.nodes).some((q) => q.ix === ix) ||
      p.nodes.some((n) => n.ix === ix),
  );
}

// ---------------------------------------------------------------------------
// Answers: what the inputs show and what Formplayer takes
// ---------------------------------------------------------------------------

/** For <input type="datetime-local">: "2026-12-01T09:30:00+01:00" → "2026-12-01T09:30". */
export function datetimeInputValue(answer: Answer | undefined): string {
  if (typeof answer !== 'string') return '';
  const m = answer.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/);
  return m ? `${m[1]}T${m[2]}` : '';
}

/** "2026-12-01T09:30" from the input → "2026-12-01T09:30:00", as DateUtils.parseDateTime takes it. */
export function datetimeAnswer(value: string): Answer {
  if (!value) return null;
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value) ? `${value}:00` : value;
}

/** For <input type="time">: "14:30" (Formplayer may send "14:30:00"). */
export function timeInputValue(answer: Answer | undefined): string {
  if (typeof answer !== 'string') return '';
  const m = answer.match(/^(\d{1,2}):(\d{2})/);
  return m ? `${m[1].padStart(2, '0')}:${m[2]}` : '';
}

/** What a typed number becomes; `undefined` when it isn't one (shown as a local error, not sent). */
export function numberAnswer(
  raw: string,
  kind: 'int' | 'longint' | 'decimal',
): Answer | undefined {
  const value = raw.trim();
  if (value === '') return null;
  if (kind === 'decimal') {
    if (!/^-?(\d+\.?\d*|\.\d+)$/.test(value)) return undefined;
    const n = Number(value);
    return Number.isFinite(n) ? n : undefined;
  }
  if (!/^-?\d+$/.test(value)) return undefined;
  const limit = kind === 'int' ? 2 ** 31 - 1 : Number.MAX_SAFE_INTEGER;
  const n = Number(value);
  return Math.abs(n) <= limit ? n : undefined;
}

export function selectedOrdinals(answer: Answer | undefined): number[] {
  if (Array.isArray(answer))
    return answer.map(Number).filter((n) => Number.isInteger(n) && n > 0);
  if (typeof answer === 'number') return [answer];
  if (typeof answer === 'string' && answer.trim()) {
    return answer
      .replace(/[[\]]/g, '')
      .split(/[\s,]+/)
      .map(Number)
      .filter((n) => Number.isInteger(n) && n > 0);
  }
  return [];
}

/** Toggle a choice (1-based) in a multiselect answer, keeping choice order. */
export function toggleChoice(
  answer: Answer | undefined,
  ordinal: number,
): number[] {
  const set = new Set(selectedOrdinals(answer));
  if (set.has(ordinal)) set.delete(ordinal);
  else set.add(ordinal);
  return [...set].sort((a, b) => a - b);
}

/** A geo answer as [lat, lon], or null. */
export function geoValue(answer: Answer | undefined): [number, number] | null {
  if (Array.isArray(answer) && answer.length >= 2) {
    const [lat, lon] = answer.map(Number);
    if (Number.isFinite(lat) && Number.isFinite(lon)) return [lat, lon];
  }
  if (typeof answer === 'string') {
    const parts = answer
      .replace(/[[\],]/g, ' ')
      .trim()
      .split(/\s+/)
      .map(Number);
    if (parts.length >= 2 && parts.every(Number.isFinite))
      return [parts[0], parts[1]];
  }
  return null;
}

/** The form's meta/location from a browser position: "lat lon altitude accuracy" (labs-mode.md). */
export function metaLocation(coords: {
  latitude: number;
  longitude: number;
  altitude?: number | null;
  accuracy?: number | null;
}): string {
  const round = (n: number, d: number) => Number(n.toFixed(d));
  return [
    round(coords.latitude, 7),
    round(coords.longitude, 7),
    round(coords.altitude ?? 0, 2),
    round(coords.accuracy ?? 0, 2),
  ].join(' ');
}

/** Choices filtered for a combobox: every word typed must start a word of the choice (case-insensitive). */
export function filterChoices(
  choices: string[],
  query: string,
): Array<{ label: string; ordinal: number }> {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  return choices
    .map((label, i) => ({ label, ordinal: i + 1 }))
    .filter(({ label }) => {
      if (!words.length) return true;
      const lw = label.toLowerCase();
      return words.every((w) => lw.includes(w));
    });
}

/** Errors from a submit-all validation-error: {ix: {status, type}} → {ix: message}. */
export function submitErrors(
  errors: Record<string, { type?: string; reason?: string }> | null | undefined,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [ix, e] of Object.entries(errors || {})) {
    out[ix] =
      e?.type === 'required'
        ? 'An answer is required.'
        : e?.reason || 'This answer is outside the allowed range.';
  }
  return out;
}
