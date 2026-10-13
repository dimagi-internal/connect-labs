// @vitest-environment happy-dom
// What a report's `view.shareSelection` tells the canopy agent: from a render drawn
// in the sandbox frame (a bridge request, host.ts) and from one drawn directly, to
// the SDK panel's `window.canopyHost` -- whose `updatePageState` here is the SDK's
// own semantics (canopy_host/panel.html: merge into the declared state, keep the
// resource and the backing tool). canopy reads the result as `current_page` /
// `read_page_state`.
import { describe, it, expect } from 'vitest';
import { createAgentSelection } from './agentSelection';
import { createDispatcher } from './sandbox/host';

const LABS = 'https://labs.connect.dimagi.com';

/** canopy_host/panel.html's host, as the SDK ships it (0.7). */
function sdkHost(declaredState) {
  let declared = declaredState;
  const pushed = [];
  const patches = [];
  const host = {
    pageState: () => (declared ? JSON.parse(JSON.stringify(declared)) : null),
    updatePageState(patch) {
      patches.push(JSON.parse(JSON.stringify(patch)));
      if (!declared) return null;
      const next = { ...declared, ...(patch || {}) };
      next.resource = declared.resource;
      if ('backing_tool' in declared) next.backing_tool = declared.backing_tool;
      declared = next;
      pushed.push(JSON.parse(JSON.stringify(declared)));
      return host.pageState();
    },
  };
  return { host, pushed, patches, current: () => declared };
}

const PAGE_STATE = {
  resource: 'labs-page://organization/acme/home',
  visible_ids: [],
  backing_tool: 'page_context',
  filters: {
    definition_id: 47,
    scope_type: 'organization',
    scope_key: 'acme',
    program_id: 25,
  },
  path: '/labs/p/org/acme/',
};

function pageHandlers(shareSelection) {
  return {
    querySupply: async () => ({}),
    queryWorkflow: async () => ({}),
    navigate: () => undefined,
    shareSelection,
    resize: () => undefined,
    copyText: async () => undefined,
    confirm: async () => true,
    download: () => undefined,
    reportError: () => undefined,
  };
}

describe('shareSelection reaches the canopy panel', () => {
  it('from the sandbox frame: the frame narrows the view, never what the agent may do', async () => {
    const sdk = sdkHost(PAGE_STATE);
    window.canopyHost = sdk.host;
    const share = createAgentSelection(window, document);
    const d = createDispatcher(pageHandlers(share), {
      labsOrigin: LABS,
      hasActivation: () => false,
    });
    const out = await d({
      id: 1,
      method: 'shareSelection',
      params: {
        visible_ids: ['10::amina', 11, { no: 1 }],
        drilled: { opportunity: 'Kano', bad: { nested: true } },
        // What a page's author might try: none of it may reach canopy.
        resource: 'labs-workflow://99/runs/1',
        backing_tool: 'workflow_run_action',
        filters: { program_id: 99 },
        path: '/labs/mcp/tokens/',
      },
    });
    expect(out.error).toBeUndefined();
    // What labs sends the SDK, before the SDK's own pinning: only the selection, the
    // server's filters with the drill under them -- never a resource, tool or path.
    expect(sdk.patches).toEqual([
      {
        filters: { ...PAGE_STATE.filters, drilled: { opportunity: 'Kano' } },
        visible_ids: ['10::amina', '11'],
      },
    ]);
    const state = sdk.current();
    expect(state.resource).toBe(PAGE_STATE.resource);
    expect(state.backing_tool).toBe('page_context');
    expect(state.path).toBe(PAGE_STATE.path);
    expect(state.visible_ids).toEqual(['10::amina', '11']);
    // The server's filters stand; the drill rides under them.
    expect(state.filters).toEqual({
      ...PAGE_STATE.filters,
      drilled: { opportunity: 'Kano' },
    });
    delete window.canopyHost;
  });

  it('a later drill keeps the server filters, not the previous drill', () => {
    const sdk = sdkHost(PAGE_STATE);
    window.canopyHost = sdk.host;
    const share = createAgentSelection(window, document);
    share({ visible_ids: ['a'], drilled: { worker: 'a' } });
    share({ visible_ids: ['b'], drilled: { worker: 'b' } });
    expect(sdk.current().filters).toEqual({
      ...PAGE_STATE.filters,
      drilled: { worker: 'b' },
    });
    expect(sdk.pushed).toHaveLength(2);
    delete window.canopyHost;
  });

  it('a selection shared before the panel loaded is sent once it has', () => {
    const share = createAgentSelection(window, document);
    share({ visible_ids: ['early'], drilled: {} });
    const sdk = sdkHost(PAGE_STATE);
    window.canopyHost = sdk.host;
    document.dispatchEvent(new CustomEvent('canopy:ready'));
    expect(sdk.current().visible_ids).toEqual(['early']);
    delete window.canopyHost;
  });

  it('no panel (the definition does not share): nothing happens', () => {
    const share = createAgentSelection(window, document);
    expect(() => share({ visible_ids: ['x'] })).not.toThrow();
  });
});
