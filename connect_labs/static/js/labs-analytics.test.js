// @vitest-environment happy-dom
// What labs hands Umami: a route template, never a raw URL. Paths and query
// strings carry frontline-worker usernames and record ids; none may survive
// labsAnalyticsBeforeSend.
import { describe, it, expect, beforeEach, vi } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

const SRC = fs.readFileSync(path.join(__dirname, 'labs-analytics.js'), 'utf8');

function load(pathname, cfg) {
  window.history.replaceState({}, '', pathname);
  document.body.innerHTML = '';
  if (cfg) {
    const el = document.createElement('script');
    el.type = 'application/json';
    el.id = 'labs-analytics-data';
    el.textContent = JSON.stringify(cfg);
    document.body.appendChild(el);
  }
  // eslint-disable-next-line no-new-func
  new Function(SRC)();
  return window.labsAnalyticsBeforeSend;
}

const ORIGIN = () => window.location.origin;

describe('labsAnalyticsBeforeSend', () => {
  beforeEach(() => {
    delete window.labsAnalyticsBeforeSend;
  });

  it('uses the server route template for the rendered page and drops the query', () => {
    const send = load('/campaign/api/users/flw_jane/role/', {
      route: '/campaign/api/users/{username}/role/',
    });
    const out = send('event', {
      url:
        ORIGIN() +
        '/campaign/api/users/flw_jane/role/?username=flw_jane&tab=visits',
      referrer: '',
      title: 'Jane Doe — role',
    });
    expect(out.url).toBe(ORIGIN() + '/campaign/api/users/{username}/role/');
    expect(JSON.stringify(out)).not.toContain('jane');
    expect('title' in out).toBe(false);
  });

  it('templates ids heuristically after a client-side navigation', () => {
    const send = load('/labs/workflow/12/', {
      route: '/labs/workflow/{workflow_id}/',
    });
    const out = send('event', {
      url: '/labs/workflow/12/run/9f8e7d6c-1234-4abc-9def-0123456789ab/?username=x',
    });
    expect(out.url).toBe('/labs/workflow/{id}/run/{id}/');
  });

  it('templates FLW identifiers after known entity prefixes even when they look like words', () => {
    const tp = load('/', null) && window.labsAnalyticsTemplatePath;
    expect(tp('/campaign/api/users/jane/role/')).toBe(
      '/campaign/api/users/{id}/role/',
    );
    expect(tp('/campaign/api/kyc/abc/status/')).toBe(
      '/campaign/api/kyc/{id}/status/',
    );
    expect(tp('/labs/opportunity/812/worker/flw1234/')).toBe(
      '/labs/opportunity/{id}/worker/{id}/',
    );
    expect(tp('/x/jane%40example.org/')).toBe('/x/{id}/');
    expect(tp('/supply/u/AbCdEfGhIjKlMnOpQrStUv/')).toBe('/supply/u/{id}/');
    expect(tp('/labs/data/')).toBe('/labs/data/');
  });

  it('templates a same-site referrer and keeps only the origin of another site', () => {
    const send = load('/labs/', { route: '/labs/' });
    const same = send('pageview', {
      url: '/labs/',
      referrer: ORIGIN() + '/labs/workflow/44/?username=jane',
    });
    expect(same.referrer).toBe(ORIGIN() + '/labs/workflow/{id}/');
    const other = send('pageview', {
      url: '/labs/',
      referrer: 'https://mail.example.com/inbox/jane?id=7',
    });
    expect(other.referrer).toBe('https://mail.example.com/');
  });

  it('works when analytics is unconfigured (no config element)', () => {
    const send = load('/labs/workflow/5/', null);
    expect(send('pageview', { url: '/labs/workflow/5/?q=secret' }).url).toBe(
      '/labs/workflow/{id}/',
    );
  });
});
