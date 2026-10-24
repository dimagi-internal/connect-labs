// Every MCP Apps View's markup is well formed where a slip costs the whole card: a
// stray tag inside <style> turns the first rule's selector invalid, the browser drops
// it, and every custom property the View is drawn with (font, colours, borders) goes
// undefined -- a serif, borderless card whose sticky header has no background.
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { Window } from 'happy-dom';

const DIR = new URL('./', import.meta.url);
const VIEWS = readdirSync(DIR).filter((f) => f.endsWith('.html'));

function blocks(html, tag) {
  const re = new RegExp(`<${tag}\\b[^>]*>([\\s\\S]*?)</${tag}>`, 'gi');
  return [...html.matchAll(re)].map((m) => m[1]);
}

describe.each(VIEWS)('%s', (name) => {
  const html = readFileSync(new URL(name, DIR), 'utf8');

  it('has no tag nested inside a <style> block', () => {
    const styles = blocks(html, 'style');
    expect(styles.length).toBeGreaterThan(0);
    for (const css of styles) expect(css).not.toMatch(/<\/?[a-z]/i);
  });

  it('has no <style> or <script> opened inside a <script> block', () => {
    for (const js of blocks(html, 'script'))
      expect(js).not.toMatch(/<(style|script)\b/i);
  });

  it('declares its custom properties on :root, and the browser keeps them', () => {
    // The View's stylesheet alone, in an empty page: none of its script runs.
    const window = new Window();
    const style = window.document.createElement('style');
    style.textContent = blocks(html, 'style').join('\n');
    window.document.head.appendChild(style);
    const root = window.getComputedStyle(window.document.documentElement);
    expect(root.getPropertyValue('--font-sans')).toMatch(/sans-serif/);
    expect(window.getComputedStyle(window.document.body).fontFamily).toMatch(
      /system-ui|sans-serif/,
    );
  });
});

it('finds every View', () => {
  expect(VIEWS).toEqual(
    expect.arrayContaining([
      'data_result.html',
      'pmc_states.html',
      'workflow_action_preview.html',
      'workflow_edit_confirm.html',
    ]),
  );
});
