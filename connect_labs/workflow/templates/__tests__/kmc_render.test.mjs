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
