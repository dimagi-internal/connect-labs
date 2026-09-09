// The KMC render is a JSX template string injected into a page and transpiled by
// Babel IN THE BROWSER. Nothing in the Python suite can execute it — its own
// ES5-dialect test says so — so for years the only way to learn that this file was
// broken was to open the dashboard.
//
// That is the gap that let a duplicated 87-line block reach main, and it is the
// risk in any edit here. These two checks are cheap and catch the whole class:
// a file that does not parse, and a name that does not resolve.

import { test } from 'node:test';
import assert from 'node:assert';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
// Only @babel/core, which is already a devDependency and re-exports both — adding
// @babel/parser and @babel/traverse as direct deps would change package-lock and
// trigger the node image build for a test that needs neither.
import babel from '@babel/core';

const { parseSync, traverse } = babel;
const HERE = dirname(fileURLToPath(import.meta.url));
const RENDER = join(HERE, '..', 'kmc_programme_metrics_render.js');
const src = readFileSync(RENDER, 'utf8');

function ast() {
  return parseSync(src, {
    filename: 'kmc_programme_metrics_render.jsx',
    presets: [['@babel/preset-react']],
    configFile: false,
    babelrc: false,
  });
}

test('the render parses as JSX', () => {
  assert.doesNotThrow(ast);
});

test('every referenced name resolves', () => {
  // A rewire that misses a call site leaves an identifier bound to nothing. In the
  // browser that is a ReferenceError at render time — a blank dashboard — and it is
  // invisible to every other check we have.
  const GLOBALS = new Set([
    'React',
    'window',
    'document',
    'fetch',
    'console',
    'Object',
    'Array',
    'String',
    'Number',
    'Boolean',
    'Math',
    'JSON',
    'Date',
    'isNaN',
    'parseInt',
    'parseFloat',
    'Set',
    'Map',
    'Promise',
    'encodeURIComponent',
    'undefined',
    'NaN',
    'navigator',
    'setTimeout',
    'alert',
    'confirm',
    'Intl',
    'RegExp',
    'Error',
  ]);
  const missing = new Map();
  traverse(ast(), {
    ReferencedIdentifier(path) {
      const n = path.node.name;
      if (GLOBALS.has(n) || path.scope.hasBinding(n, true)) return;
      if (!missing.has(n)) missing.set(n, path.node.loc?.start.line ?? 0);
    },
  });
  assert.deepStrictEqual(
    [...missing].map(([n, l]) => `${n} (line ${l})`),
    [],
    'unresolved identifiers would throw at render time',
  );
});

test('no top-level declaration appears twice', () => {
  // `var` redeclaration is legal, so a re-inserted block is silent: the second
  // assignment wins and the first becomes dead. #1467 duplicated nine declarations
  // this way, three of them React hooks. Mirrors the Python guard so the JS side
  // catches it too.
  const names = [
    ...src.matchAll(/^  (?:var|let|const|function)\s+([A-Za-z_$][\w$]*)/gm),
  ].map((m) => m[1]);
  const dupes = [
    ...new Set(names.filter((n) => names.filter((x) => x === n).length > 1)),
  ].sort();
  assert.deepStrictEqual(dupes, []);
});

test('credibility is never derived in the render', () => {
  // `<measure>_suppressed` is `(props.llo IS NULL OR props.llo NOT IN (credible))`,
  // and props.llo is NULL in every grouping set that does not group BY llo, so
  // trusting the column outside the llo scope rendered "recording not credible"
  // on the programme card. The render used to guard that with a single reader
  // (cCredibleSet); it now derives NO credibility at all -- every graded cell and
  // every credible-recorder pool arrives in the payload, decided server-side by
  // semantic/gates.py against the bound registry. A raw-column read appearing
  // here again would be that second copy coming back.
  const reads = [...src.matchAll(/_suppressed/g)].length;
  assert.strictEqual(
    reads,
    0,
    'the render must not read _suppressed; credibility is decided server-side',
  );
  assert.ok(
    !src.includes('function cCredibleSet'),
    'cCredibleSet is a second copy of the gate',
  );
});

test('every memo that reads the payload-carried registry facts depends on the payload', () => {
  // Everything this render shows comes off ONE payload (`payload`, aliased `P`):
  // a completed run's stored snapshot, or a live run's fetched preview of the
  // same thing. Any memo reading it must list it as a dependency -- otherwise the
  // memo keeps a result computed BEFORE the payload arrived, forever, with no
  // error.
  //
  // That has now happened twice. `byLLO`/`byFLW`/`byOpp` missed it and were saved
  // only by React batching `setServedFacts` with `setCSeries`; `derived` missed it
  // and was not saved, so a live run showed LLO names as "opp 10013".."opp 10042"
  // while the server had delivered 23 llo_map entries and 1,260 semantic rows.
  //
  // Reviewing dependency arrays by eye is exactly what failed, so this walks them.
  const ROOTS = ['LLO_OF', 'P'];
  const tree = ast();

  // name -> identifiers its body references, for functions declared in this file.
  const refs = new Map();
  traverse(tree, {
    FunctionDeclaration(path) {
      const name = path.node.id && path.node.id.name;
      if (!name) return;
      const seen = new Set();
      path.traverse({
        Identifier(inner) {
          seen.add(inner.node.name);
        },
      });
      refs.set(name, seen);
    },
  });

  // Transitive closure: which helpers end up touching a root fact.
  const touches = new Set(ROOTS);
  for (let changed = true; changed; ) {
    changed = false;
    for (const [name, seen] of refs) {
      if (touches.has(name)) continue;
      for (const s of seen) {
        if (touches.has(s)) {
          touches.add(name);
          changed = true;
          break;
        }
      }
    }
  }

  const offenders = [];
  traverse(tree, {
    CallExpression(path) {
      const callee = path.node.callee;
      const isMemo =
        callee.type === 'MemberExpression' &&
        callee.object.name === 'React' &&
        (callee.property.name === 'useMemo' ||
          callee.property.name === 'useCallback');
      if (!isMemo) return;
      const [fn, deps] = path.node.arguments;
      if (!fn || !deps || deps.type !== 'ArrayExpression') return;

      let reads = null;
      path.get('arguments.0').traverse({
        Identifier(inner) {
          if (!reads && touches.has(inner.node.name)) reads = inner.node.name;
        },
      });
      if (!reads) return;

      const declared = deps.elements.map((e) => (e && e.name) || '');
      if (!declared.includes('payload') && !declared.includes('P')) {
        offenders.push(
          `line ${
            fn.loc ? fn.loc.start.line : '?'
          }: reads ${reads} but depends on [${declared.join(', ')}]`,
        );
      }
    },
  });

  assert.deepStrictEqual(
    offenders,
    [],
    'these memos read the payload without depending on it:\n  ' +
      offenders.join('\n  '),
  );
});
