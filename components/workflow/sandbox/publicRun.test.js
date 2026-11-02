// @vitest-environment happy-dom
// @vitest-environment-options {"settings": {"disableIframePageLoading": true}}
// A public link draws the copied run in the ordinary frame, and the frame has nothing to ask.
import { describe, it, expect } from 'vitest';
import { createDispatcher } from './host';
import { publicRunPayload } from './publicRun';

const DATA = {
  labsOrigin: 'https://labs.connect.dimagi.com',
  mapboxToken: 'pk.invented',
  payload: {
    renderCode: 'function WorkflowUI() { return null; }',
    definition: { id: 11, name: 'Battery report', config: {} },
    instance: {
      id: 22,
      status: 'completed',
      completed_at: '2026-01-08T00:00:00+00:00',
      state: { seen: true },
      snapshot: { pipelines: { visits: { rows: [{ note: 'a' }] } } },
    },
    workers: [{ username: 'worker-one' }],
    pipelines: { visits: { rows: [{ note: 'a' }] } },
  },
};

describe('a public run link', () => {
  it('hands the frame a finished run and no way to act', () => {
    const p = publicRunPayload(DATA);
    expect(p.mode).toBe('workflow');
    expect(p.view).toEqual({
      state: { seen: true },
      isCompleted: true,
      asOf: '2026-01-08T00:00:00+00:00',
    });
    expect(p.pipelines.visits.rows).toHaveLength(1);
    expect(p.workers).toHaveLength(1);
    expect(p.apiEndpoints).toEqual({});
    expect(p.workflowActions).toEqual([]);
    expect(p.mapboxToken).toBe('pk.invented');
  });

  it('does not hand the frame the address, which carries the token', () => {
    expect(publicRunPayload(DATA).pageUrl).toBe('');
  });

  it('gets no answer from labs: with no workflow handlers the host answers no fetch or write', async () => {
    const dispatch = createDispatcher(
      {
        querySupply: () => Promise.reject(new Error('no')),
        queryWorkflow: () => Promise.reject(new Error('no')),
      },
      { mode: 'workflow' },
    );
    for (const [method, params] of [
      ['fetch', { url: '/labs/workflow/api/run/22/', method: 'GET' }],
      ['updateState', { state: {} }],
      ['completeRun', { confirm: '' }],
      ['runAction', { key: 'coach', args: {} }],
    ]) {
      const answer = await dispatch({ id: 1, method, params });
      expect(answer.error, method).toBeTruthy();
      expect(answer.result, method).toBeUndefined();
    }
  });
});
