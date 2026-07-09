// Unit tests for the pure logic of the shared FLW audit-breakdown primitive.
import { describe, it, expect } from 'vitest';

import LabsAudit from './labs_audit_breakdown.js';

const { groupByOppFlw, oppSummary, bulkUrl } = LabsAudit;

function session(over) {
  return Object.assign(
    {
      id: 1,
      opportunity_id: 1973,
      flw_username: 'flw1',
      flw_display_name: 'Field Worker One',
      tag: 'muac',
      image_count: 10,
      status: 'in_progress',
      assessment_stats: { pass: 0, fail: 0, ai_match: 0, ai_no_match: 0 },
    },
    over,
  );
}

describe('groupByOppFlw', () => {
  it('groups by opportunity then field worker, splitting muac/rest tracks', () => {
    const sessions = [
      session({ id: 1, tag: 'muac' }),
      session({ id: 2, tag: 'rest', image_count: 3 }),
      session({
        id: 3,
        opportunity_id: 1976,
        flw_username: 'flw2',
        flw_display_name: 'Two',
        tag: 'muac',
      }),
    ];
    const grouped = groupByOppFlw(sessions);
    expect(Object.keys(grouped).sort()).toEqual(['1973', '1976']);
    expect(grouped['1973'].order).toEqual(['flw1']);
    expect(grouped['1973'].flws.flw1.muac.id).toBe(1);
    expect(grouped['1973'].flws.flw1.rest.id).toBe(2);
    expect(grouped['1973'].flws.flw1.name).toBe('Field Worker One');
    expect(grouped['1976'].flws.flw2.muac.id).toBe(3);
    expect(grouped['1976'].flws.flw2.rest).toBeNull();
  });

  it('falls back to username, then "unknown", for missing identity', () => {
    const grouped = groupByOppFlw([
      session({
        id: 9,
        flw_display_name: null,
        flw_username: 'onlyuser',
        opportunity_id: null,
        tag: 'rest',
      }),
    ]);
    expect(grouped.unknown.flws.onlyuser.name).toBe('onlyuser');
  });

  it('tolerates empty / nullish input', () => {
    expect(groupByOppFlw()).toEqual({});
    expect(groupByOppFlw([])).toEqual({});
  });
});

describe('oppSummary', () => {
  it('rolls up images, AI-reviewed, flagged, and human-reviewed per opp', () => {
    const grouped = groupByOppFlw([
      session({
        id: 1,
        tag: 'muac',
        image_count: 10,
        assessment_stats: { ai_match: 6, ai_no_match: 2, pass: 0, fail: 0 },
      }),
      session({
        id: 2,
        tag: 'rest',
        image_count: 4,
        assessment_stats: { pass: 3, fail: 1, ai_match: 0, ai_no_match: 0 },
      }),
    ]);
    const sum = oppSummary(grouped['1973']);
    expect(sum).toEqual({
      flws: 1,
      muacImages: 10,
      muacAiReviewed: 8, // ai_match + ai_no_match
      muacFlagged: 2, // ai_no_match
      restImages: 4,
      restReviewed: 4, // pass + fail
    });
  });
});

describe('bulkUrl', () => {
  it('builds the bulk review deep-link with opp + run params', () => {
    expect(bulkUrl({ id: 42, opportunity_id: 1973 }, 6128)).toBe(
      '/audit/42/bulk/?opportunity_id=1973&workflow_run_id=6128',
    );
  });

  it('omits params that are absent', () => {
    expect(bulkUrl({ id: 42, opportunity_id: null })).toBe('/audit/42/bulk/?');
  });
});
