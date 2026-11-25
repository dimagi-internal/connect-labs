/**
 * One input per kind of question (tree.widgetFor), mobile-first: every target is at least 44px,
 * every input has a label, an error is announced and tied to its input (aria-describedby).
 *
 * Text and numbers are saved as you go (debounced, and at once on blur); choices, dates and the
 * rest are saved on change. The tree Formplayer answers with is the truth: inputs show its answer
 * except while someone is typing into them.
 */

import React, {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from 'react';

import { markdownToHtml } from './text';
import type { Answer, FormNode } from './tree';
import {
  UNSUPPORTED_WIDGETS,
  datetimeAnswer,
  datetimeInputValue,
  filterChoices,
  geoValue,
  isGroup,
  isJuncture,
  isQuestion,
  isRepeatInstance,
  isRepeatPlaceholder,
  isRequired,
  numberAnswer,
  selectedOrdinals,
  timeInputValue,
  toggleChoice,
  widgetFor,
} from './tree';

export interface QuestionHandlers {
  /** Save an answer. `debounced` = text being typed. */
  answer: (ix: string, value: Answer) => Promise<void>;
  photo: (ix: string, file: File) => Promise<void>;
  newRepeat: (ix: string) => void;
  deleteRepeat: (ix: string) => void;
  /** Text inputs register how to save what they hold, so Next / Submit can wait for them. */
  registerFlush: (ix: string, flush: (() => Promise<void>) | null) => void;
  /** A problem with what was typed, before anything is sent. */
  localError: (ix: string, message: string | null) => void;
  photoPreview: (ix: string) => string | null;
  busy: boolean;
}

const DEBOUNCE_MS = 700;

const inputClass =
  'block w-full min-h-12 rounded-xl border border-slate-300 bg-white px-3 py-2 text-base text-slate-900 shadow-sm ' +
  'focus:border-indigo-600 focus:outline-none focus:ring-2 focus:ring-indigo-600/30 aria-[invalid=true]:border-red-500 ' +
  'disabled:bg-slate-100';

const choiceRow =
  'flex min-h-12 w-full cursor-pointer items-center gap-3 rounded-xl border px-3 py-2 text-left text-base ' +
  'focus-within:ring-2 focus-within:ring-indigo-600/40';

export function Caption({
  node,
  as: Tag = 'span',
  className = '',
}: {
  node: FormNode;
  as?: any;
  className?: string;
}) {
  if (node.caption_markdown) {
    return (
      <Tag
        className={className}
        dangerouslySetInnerHTML={{
          __html: markdownToHtml(node.caption_markdown),
        }}
      />
    );
  }
  return (
    <Tag className={`${className} whitespace-pre-line`}>
      {node.caption || ''}
    </Tag>
  );
}

function Help({ text }: { text?: string | null }) {
  if (!text) return null;
  return (
    <details className="mt-1 text-sm text-slate-600">
      <summary className="inline-flex min-h-11 cursor-pointer items-center gap-1 font-medium text-indigo-800">
        <i className="fa-solid fa-circle-question" aria-hidden="true" /> Help
      </summary>
      <p className="whitespace-pre-line pb-1">{text}</p>
    </details>
  );
}

// ---------------------------------------------------------------------------
// Typed text and numbers: local while typing, saved debounced
// ---------------------------------------------------------------------------

function useDraft(
  q: FormNode,
  handlers: QuestionHandlers,
  toText: (a: Answer | undefined) => string,
  toAnswer: (text: string) => Answer | undefined,
  invalidMessage = '',
) {
  const [text, setText] = useState(() => toText(q.answer));
  const dirty = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latest = useRef(text);
  latest.current = text;

  // Follow the tree when nobody is typing here.
  useEffect(() => {
    if (!dirty.current) setText(toText(q.answer));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q.answer]);

  const flush = useCallback(async () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    if (!dirty.current) return;
    const value = toAnswer(latest.current);
    if (value === undefined) {
      handlers.localError(q.ix, invalidMessage || 'Check this answer.');
      return;
    }
    handlers.localError(q.ix, null);
    dirty.current = false;
    await handlers.answer(q.ix, value);
  }, [handlers, q.ix, toAnswer, invalidMessage]);

  useEffect(() => {
    handlers.registerFlush(q.ix, flush);
    return () => handlers.registerFlush(q.ix, null);
  }, [handlers, q.ix, flush]);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const onChange = (value: string) => {
    setText(value);
    dirty.current = true;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      void flush();
    }, DEBOUNCE_MS);
  };
  return { text, onChange, onBlur: () => void flush() };
}

const asText = (a: Answer | undefined) =>
  a === null || a === undefined ? '' : String(a);

function TextQuestion({
  q,
  id,
  describedBy,
  handlers,
  kind,
}: InputProps & { kind: 'text' | 'phone' }) {
  const toAnswer = useCallback((t: string) => (t === '' ? null : t), []);
  const { text, onChange, onBlur } = useDraft(q, handlers, asText, toAnswer);
  const long =
    q.style?.raw === 'full' || (q.answer && String(q.answer).length > 80);
  const common = {
    id,
    value: text,
    onChange: (e: any) => onChange(e.target.value),
    onBlur,
    'aria-describedby': describedBy,
    'aria-invalid': describedBy.includes('-error') || undefined,
    'aria-required': isRequired(q) || undefined,
    className: inputClass,
  };
  if (kind === 'text' && long) return <textarea rows={4} {...common} />;
  return (
    <input
      type={kind === 'phone' ? 'tel' : 'text'}
      inputMode={kind === 'phone' ? 'numeric' : undefined}
      autoComplete="off"
      enterKeyHint="next"
      {...common}
    />
  );
}

function NumberQuestion({
  q,
  id,
  describedBy,
  handlers,
  kind,
}: InputProps & { kind: 'int' | 'longint' | 'decimal' }) {
  const toAnswer = useCallback((t: string) => numberAnswer(t, kind), [kind]);
  const message =
    kind === 'decimal' ? 'Enter a number.' : 'Enter a whole number.';
  const { text, onChange, onBlur } = useDraft(
    q,
    handlers,
    asText,
    toAnswer,
    message,
  );
  return (
    <input
      id={id}
      type="text"
      inputMode={kind === 'decimal' ? 'decimal' : 'numeric'}
      pattern={kind === 'decimal' ? '-?[0-9]*[.]?[0-9]*' : '-?[0-9]*'}
      autoComplete="off"
      enterKeyHint="next"
      value={text}
      onChange={(e) => onChange(e.target.value)}
      onBlur={onBlur}
      aria-describedby={describedBy}
      aria-invalid={describedBy.includes('-error') || undefined}
      aria-required={isRequired(q) || undefined}
      className={inputClass}
    />
  );
}

// ---------------------------------------------------------------------------
// Answered on change
// ---------------------------------------------------------------------------

interface InputProps {
  q: FormNode;
  id: string;
  describedBy: string;
  handlers: QuestionHandlers;
}

function DateLike({
  q,
  id,
  describedBy,
  handlers,
  kind,
}: InputProps & { kind: 'date' | 'time' | 'datetime' }) {
  const fromTree =
    kind === 'date'
      ? typeof q.answer === 'string'
        ? q.answer.slice(0, 10)
        : ''
      : kind === 'time'
        ? timeInputValue(q.answer)
        : datetimeInputValue(q.answer);
  const [value, setValue] = useState(fromTree);
  useEffect(() => setValue(fromTree), [fromTree]);
  const save = (v: string) => {
    setValue(v);
    const answer =
      v === '' ? null : kind === 'datetime' ? datetimeAnswer(v) : v;
    void handlers.answer(q.ix, answer);
  };
  return (
    <div className="flex gap-2">
      <input
        id={id}
        type={kind === 'datetime' ? 'datetime-local' : kind}
        value={value}
        onChange={(e) => save(e.target.value)}
        aria-describedby={describedBy}
        aria-invalid={describedBy.includes('-error') || undefined}
        aria-required={isRequired(q) || undefined}
        className={inputClass}
      />
      {kind === 'date' && !value && (
        <button
          type="button"
          className="min-h-12 shrink-0 rounded-xl border border-indigo-300 bg-white px-3 text-sm font-semibold text-indigo-800"
          onClick={() =>
            save(
              new Date(Date.now() - new Date().getTimezoneOffset() * 60000)
                .toISOString()
                .slice(0, 10),
            )
          }
        >
          Today
        </button>
      )}
    </div>
  );
}

function SelectOne({ q, id, describedBy, handlers }: InputProps) {
  const selected = selectedOrdinals(q.answer)[0] ?? null;
  const [optimistic, setOptimistic] = useState<number | null>(selected);
  useEffect(() => setOptimistic(selected), [selected]);
  const choose = (n: number | null) => {
    setOptimistic(n);
    void handlers.answer(q.ix, n);
  };
  return (
    <div
      role="radiogroup"
      aria-labelledby={`${id}-label`}
      aria-describedby={describedBy}
      className="space-y-2"
    >
      {(q.choices || []).map((label, i) => {
        const n = i + 1;
        const on = optimistic === n;
        return (
          <label
            key={n}
            className={`${choiceRow} ${on ? 'border-indigo-600 bg-indigo-50 font-medium text-indigo-950' : 'border-slate-300 bg-white'}`}
          >
            <input
              type="radio"
              name={id}
              className="h-5 w-5 shrink-0 accent-indigo-700"
              checked={on}
              onChange={() => choose(n)}
            />
            <span className="min-w-0 break-words">{label}</span>
          </label>
        );
      })}
      {optimistic !== null && !isRequired(q) && (
        <button
          type="button"
          className="min-h-11 text-sm font-medium text-slate-600 underline"
          onClick={() => choose(null)}
        >
          Clear the answer
        </button>
      )}
    </div>
  );
}

function SelectMany({ q, id, describedBy, handlers }: InputProps) {
  const fromTree = selectedOrdinals(q.answer);
  const key = fromTree.join(',');
  const [chosen, setChosen] = useState<number[]>(fromTree);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => setChosen(fromTree), [key]);
  const toggle = (n: number) => {
    const next = toggleChoice(chosen, n);
    setChosen(next);
    void handlers.answer(q.ix, next.length ? next : null);
  };
  return (
    <div
      role="group"
      aria-labelledby={`${id}-label`}
      aria-describedby={describedBy}
      className="space-y-2"
    >
      {(q.choices || []).map((label, i) => {
        const n = i + 1;
        const on = chosen.includes(n);
        return (
          <label
            key={n}
            className={`${choiceRow} ${on ? 'border-indigo-600 bg-indigo-50 font-medium text-indigo-950' : 'border-slate-300 bg-white'}`}
          >
            <input
              type="checkbox"
              className="h-5 w-5 shrink-0 accent-indigo-700"
              checked={on}
              onChange={() => toggle(n)}
            />
            <span className="min-w-0 break-words">{label}</span>
          </label>
        );
      })}
    </div>
  );
}

function Dropdown({ q, id, describedBy, handlers }: InputProps) {
  const selected = selectedOrdinals(q.answer)[0] ?? 0;
  return (
    <select
      id={id}
      value={selected}
      onChange={(e) =>
        void handlers.answer(q.ix, Number(e.target.value) || null)
      }
      aria-describedby={describedBy}
      aria-invalid={describedBy.includes('-error') || undefined}
      aria-required={isRequired(q) || undefined}
      className={inputClass}
    >
      <option value={0}>Choose…</option>
      {(q.choices || []).map((label, i) => (
        <option key={i + 1} value={i + 1}>
          {label}
        </option>
      ))}
    </select>
  );
}

function Combobox({ q, id, describedBy, handlers }: InputProps) {
  const choices = q.choices || [];
  const selected = selectedOrdinals(q.answer)[0] ?? null;
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const matches = useMemo(
    () => filterChoices(choices, query).slice(0, 50),
    [choices, query],
  );
  const listId = `${id}-list`;
  const pick = (n: number | null) => {
    setQuery('');
    setOpen(false);
    void handlers.answer(q.ix, n);
  };
  return (
    <div>
      {selected !== null && (
        <div className="mb-2 flex items-center justify-between gap-2 rounded-xl border border-indigo-600 bg-indigo-50 px-3 py-2">
          <span className="min-w-0 break-words font-medium text-indigo-950">
            {choices[selected - 1]}
          </span>
          <button
            type="button"
            className="min-h-11 shrink-0 px-2 text-sm font-medium text-indigo-800 underline"
            onClick={() => pick(null)}
          >
            Change
          </button>
        </div>
      )}
      <input
        id={id}
        type="search"
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-describedby={describedBy}
        aria-invalid={describedBy.includes('-error') || undefined}
        placeholder={
          selected === null ? 'Type to search…' : 'Search to change…'
        }
        value={query}
        onFocus={() => setOpen(true)}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        className={inputClass}
        autoComplete="off"
      />
      {open && (
        <ul
          id={listId}
          role="listbox"
          className="mt-1 max-h-72 overflow-y-auto rounded-xl border border-slate-200 bg-white shadow"
        >
          {matches.length === 0 && (
            <li className="px-3 py-3 text-sm text-slate-500">No match</li>
          )}
          {matches.map(({ label, ordinal }) => (
            <li
              key={ordinal}
              role="option"
              aria-selected={ordinal === selected}
            >
              <button
                type="button"
                className="flex min-h-12 w-full items-center px-3 text-left hover:bg-indigo-50 focus:bg-indigo-50 focus:outline-none"
                onClick={() => pick(ordinal)}
              >
                {label}
              </button>
            </li>
          ))}
          {choices.length > 50 && matches.length === 50 && (
            <li className="px-3 py-2 text-xs text-slate-500">
              Keep typing to narrow the list.
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

function GeoQuestion({ q, describedBy, handlers }: InputProps) {
  const value = geoValue(q.answer);
  const [state, setState] = useState<{
    busy: boolean;
    error: string;
    accuracy: number | null;
  }>({
    busy: false,
    error: '',
    accuracy: null,
  });
  const capture = () => {
    if (!('geolocation' in navigator)) {
      setState({
        busy: false,
        error: "This browser can't share its location.",
        accuracy: null,
      });
      return;
    }
    setState({ busy: true, error: '', accuracy: null });
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setState({ busy: false, error: '', accuracy: pos.coords.accuracy });
        void handlers.answer(q.ix, [
          Number(pos.coords.latitude.toFixed(7)),
          Number(pos.coords.longitude.toFixed(7)),
        ]);
      },
      (err) =>
        setState({
          busy: false,
          error:
            err.code === err.PERMISSION_DENIED
              ? 'Location permission was refused. Allow it in the browser settings, then try again.'
              : "The location couldn't be found. Move to open sky and try again.",
          accuracy: null,
        }),
      { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 },
    );
  };
  return (
    <div className="space-y-2" aria-describedby={describedBy}>
      {value && (
        <p className="rounded-xl bg-slate-50 px-3 py-2 font-mono text-sm text-slate-800">
          {value[0].toFixed(6)}, {value[1].toFixed(6)}
          {state.accuracy !== null && (
            <span className="ml-2 text-slate-500">
              ±{Math.round(state.accuracy)} m
            </span>
          )}
        </p>
      )}
      <div className="flex gap-2">
        <button
          type="button"
          onClick={capture}
          disabled={state.busy}
          className="inline-flex min-h-12 flex-1 items-center justify-center gap-2 rounded-xl border border-indigo-300 bg-white px-4 font-semibold text-indigo-800 disabled:opacity-60"
        >
          <i
            className={`fa-solid ${state.busy ? 'fa-circle-notch fa-spin' : 'fa-location-crosshairs'}`}
            aria-hidden="true"
          />
          {state.busy
            ? 'Finding you…'
            : value
              ? 'Capture again'
              : 'Use my location'}
        </button>
        {value && !isRequired(q) && (
          <button
            type="button"
            className="min-h-12 px-3 text-sm font-medium text-slate-600 underline"
            onClick={() => void handlers.answer(q.ix, null)}
          >
            Clear
          </button>
        )}
      </div>
      {state.error && (
        <p className="text-sm text-amber-800" role="status">
          {state.error}
        </p>
      )}
    </div>
  );
}

function PhotoQuestion({ q, id, describedBy, handlers }: InputProps) {
  const [busy, setBusy] = useState(false);
  const preview = handlers.photoPreview(q.ix);
  const inputRef = useRef<HTMLInputElement>(null);
  const onFile = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    try {
      await handlers.photo(q.ix, file);
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = '';
    }
  };
  const hasAnswer = !!q.answer;
  return (
    <div className="space-y-2">
      {(preview || hasAnswer) && (
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-slate-50">
          {preview ? (
            <img
              src={preview}
              alt="The photo attached to this question"
              className="max-h-72 w-full object-contain"
            />
          ) : (
            <p className="px-3 py-3 text-sm text-slate-700">
              <i className="fa-solid fa-image mr-2" aria-hidden="true" />A photo
              is attached.
            </p>
          )}
        </div>
      )}
      <label
        htmlFor={id}
        className={`inline-flex min-h-12 w-full cursor-pointer items-center justify-center gap-2 rounded-xl border border-indigo-300 bg-white px-4 font-semibold text-indigo-800 focus-within:ring-2 focus-within:ring-indigo-600/40 ${busy ? 'opacity-60' : ''}`}
      >
        <i
          className={`fa-solid ${busy ? 'fa-circle-notch fa-spin' : 'fa-camera'}`}
          aria-hidden="true"
        />
        {busy
          ? 'Attaching…'
          : hasAnswer || preview
            ? 'Retake photo'
            : 'Take a photo'}
        <input
          ref={inputRef}
          id={id}
          type="file"
          accept="image/*"
          capture="environment"
          className="sr-only"
          disabled={busy}
          aria-describedby={describedBy}
          onChange={(e) => void onFile(e.target.files?.[0])}
        />
      </label>
    </div>
  );
}

const UNSUPPORTED_TEXT: Record<string, string> = {
  signature: 'Signatures',
  audio: 'Audio recordings',
  video: 'Videos',
  document: 'Document uploads',
  unsupported: 'This kind of question',
};

function Unsupported({ q, widget }: { q: FormNode; widget: string }) {
  return (
    <div
      className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-900"
      data-testid="cw-unsupported"
    >
      <p className="font-semibold">
        <i
          className="fa-solid fa-mobile-screen-button mr-2"
          aria-hidden="true"
        />
        {UNSUPPORTED_TEXT[widget] || UNSUPPORTED_TEXT.unsupported} can't be
        captured on the web yet.
      </p>
      <p className="mt-1">
        {isRequired(q)
          ? "This one is required, so this form can't be finished here. Use the CommCare app on a phone for it."
          : 'You can skip it here.'}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// A question, a group, a repeat
// ---------------------------------------------------------------------------

export function QuestionView({
  q,
  error,
  handlers,
}: {
  q: FormNode;
  error?: string;
  handlers: QuestionHandlers;
}) {
  const reactId = useId();
  const id = `cwq-${reactId.replace(/[^a-zA-Z0-9_-]/g, '')}`;
  const widget = widgetFor(q);
  const errorId = `${id}-error`;
  const hintId = `${id}-hint`;
  const describedBy = [q.hint ? hintId : '', error ? errorId : '']
    .filter(Boolean)
    .join(' ');
  const labelFor =
    ['select', 'multiselect', 'geo', 'label'].includes(widget) ||
    UNSUPPORTED_WIDGETS.includes(widget)
      ? undefined
      : id;
  const LabelTag: any = labelFor ? 'label' : 'p';

  if (widget === 'label') {
    return (
      <div
        className="rounded-xl bg-white p-4 text-base leading-relaxed shadow-sm"
        data-ix={q.ix}
      >
        <Caption node={q} as="div" />
        <Help text={q.help} />
      </div>
    );
  }

  let input: React.ReactNode;
  const props = { q, id, describedBy, handlers };
  switch (widget) {
    case 'text':
    case 'phone':
      input = <TextQuestion {...props} kind={widget} />;
      break;
    case 'int':
    case 'longint':
    case 'decimal':
      input = <NumberQuestion {...props} kind={widget} />;
      break;
    case 'date':
    case 'time':
    case 'datetime':
      input = <DateLike {...props} kind={widget} />;
      break;
    case 'select':
      input = <SelectOne {...props} />;
      break;
    case 'dropdown':
      input = <Dropdown {...props} />;
      break;
    case 'combobox':
      input = <Combobox {...props} />;
      break;
    case 'multiselect':
      input = <SelectMany {...props} />;
      break;
    case 'geo':
      input = <GeoQuestion {...props} />;
      break;
    case 'image':
      input = <PhotoQuestion {...props} />;
      break;
    default:
      input = <Unsupported q={q} widget={widget} />;
  }

  return (
    <div
      className="rounded-xl bg-white p-4 shadow-sm"
      data-ix={q.ix}
      data-widget={widget}
    >
      <LabelTag
        id={`${id}-label`}
        htmlFor={labelFor}
        className="mb-2 block text-base font-semibold leading-snug text-slate-900"
      >
        <Caption node={q} />
        {isRequired(q) && (
          <span className="ml-1 text-red-700" aria-hidden="true">
            *
          </span>
        )}
        {isRequired(q) && <span className="sr-only"> (required)</span>}
      </LabelTag>
      {q.hint && (
        <p id={hintId} className="-mt-1 mb-2 text-sm text-slate-600">
          {q.hint}
        </p>
      )}
      {input}
      {error && (
        <p
          id={errorId}
          role="alert"
          className="mt-2 flex items-start gap-1 text-sm font-medium text-red-700"
        >
          <i
            className="fa-solid fa-circle-exclamation mt-0.5"
            aria-hidden="true"
          />
          {error}
        </p>
      )}
      <Help text={q.help} />
    </div>
  );
}

export function NodeView({
  node,
  errors,
  handlers,
}: {
  node: FormNode;
  errors: Record<string, string>;
  handlers: QuestionHandlers;
}) {
  if (isQuestion(node))
    return (
      <QuestionView q={node} error={errors[node.ix]} handlers={handlers} />
    );
  if (isGroup(node)) {
    return (
      <section className="space-y-3" aria-label={node.caption || undefined}>
        {node.caption && (
          <Caption
            node={node}
            as="h3"
            className="px-1 pt-1 text-sm font-semibold uppercase tracking-wide text-slate-500"
          />
        )}
        {(node.children || []).map((child) => (
          <NodeView
            key={child.ix}
            node={child}
            errors={errors}
            handlers={handlers}
          />
        ))}
      </section>
    );
  }
  if (isRepeatInstance(node) || isRepeatPlaceholder(node) || isJuncture(node)) {
    return <RepeatView nodes={[node]} errors={errors} handlers={handlers} />;
  }
  return null;
}

/** A repeat: its instances as cards (each removable when the form allows), then "add another". */
export function RepeatView({
  nodes,
  errors,
  handlers,
}: {
  nodes: FormNode[];
  errors: Record<string, string>;
  handlers: QuestionHandlers;
}) {
  const items: React.ReactNode[] = [];
  for (const node of nodes) {
    if (isJuncture(node)) {
      for (const instance of node.children || []) {
        items.push(
          <RepeatInstance
            key={instance.ix}
            node={instance}
            canDelete
            errors={errors}
            handlers={handlers}
          />,
        );
      }
      items.push(
        <AddRepeat
          key={`${node.ix}-add`}
          ix={node.ix}
          text={node['add-choice'] || 'Add another'}
          handlers={handlers}
        />,
      );
    } else if (isRepeatPlaceholder(node)) {
      items.push(
        <AddRepeat
          key={node.ix}
          ix={node.ix}
          text={node['add-choice'] || `Add ${node.caption || 'another'}`}
          handlers={handlers}
        />,
      );
    } else {
      items.push(
        <RepeatInstance
          key={node.ix}
          node={node}
          canDelete={!!node.delete}
          errors={errors}
          handlers={handlers}
        />,
      );
    }
  }
  return <div className="space-y-3">{items}</div>;
}

function RepeatInstance({
  node,
  canDelete,
  errors,
  handlers,
}: {
  node: FormNode;
  canDelete: boolean;
  errors: Record<string, string>;
  handlers: QuestionHandlers;
}) {
  return (
    <section
      className="rounded-2xl border border-slate-200 bg-slate-50 p-3"
      aria-label={node.caption || 'Repeat'}
      data-ix={node.ix}
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <Caption node={node} as="h3" className="font-semibold text-slate-800" />
        {canDelete && (
          <button
            type="button"
            disabled={handlers.busy}
            onClick={() => {
              if (window.confirm(`Remove “${node.caption || 'this entry'}”?`))
                handlers.deleteRepeat(node.ix);
            }}
            className="inline-flex min-h-11 items-center gap-1 rounded-lg px-3 text-sm font-medium text-red-700 hover:bg-red-50"
          >
            <i className="fa-solid fa-trash-can" aria-hidden="true" /> Remove
          </button>
        )}
      </div>
      <div className="space-y-3">
        {(node.children || []).map((child) => (
          <NodeView
            key={child.ix}
            node={child}
            errors={errors}
            handlers={handlers}
          />
        ))}
      </div>
    </section>
  );
}

function AddRepeat({
  ix,
  text,
  handlers,
}: {
  ix: string;
  text: string;
  handlers: QuestionHandlers;
}) {
  return (
    <button
      type="button"
      disabled={handlers.busy}
      onClick={() => handlers.newRepeat(ix)}
      className="inline-flex min-h-12 w-full items-center justify-center gap-2 rounded-xl border-2 border-dashed border-indigo-300 bg-white px-4 font-semibold text-indigo-800 disabled:opacity-60"
    >
      <i className="fa-solid fa-plus" aria-hidden="true" /> {text}
    </button>
  );
}
