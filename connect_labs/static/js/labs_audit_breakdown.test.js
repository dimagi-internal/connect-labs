// Unit tests for the pure logic of the shared FLW audit-breakdown primitive.
import { describe, it, expect } from 'vitest';

import LabsAudit from './labs_audit_breakdown.js';

const {
  groupByOppFlw,
  oppSummary,
  bulkUrl,
  humanReviewedOf,
  duplicateFakeOf,
  clusterCountOf,
  showAiStatsOf,
  aiFlagsSummary,
} = LabsAudit;

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

  it('rolls up restReviewed including duplicate_fake (a reviewed image, just categorized as duplicate)', () => {
    const grouped = groupByOppFlw([
      session({
        id: 1,
        tag: 'rest',
        image_count: 6,
        assessment_stats: {
          pass: 2,
          fail: 1,
          duplicate_fake: 2,
          ai_match: 0,
          ai_no_match: 0,
        },
      }),
    ]);
    const sum = oppSummary(grouped['1973']);
    expect(sum.restReviewed).toBe(5); // pass + fail + duplicate_fake
  });
});

describe('humanReviewedOf / duplicateFakeOf', () => {
  it('counts duplicate_fake as reviewed, not pending', () => {
    const s = session({
      image_count: 10,
      assessment_stats: {
        pass: 4,
        fail: 2,
        duplicate_fake: 3,
        ai_match: 0,
        ai_no_match: 0,
      },
    });
    expect(humanReviewedOf(s)).toBe(9); // pass + fail + duplicate_fake
    expect(duplicateFakeOf(s)).toBe(3);
    expect(s.image_count - humanReviewedOf(s)).toBe(1); // pending
  });

  it('defaults to 0 when duplicate_fake is absent (sessions predating the field)', () => {
    const s = session({
      assessment_stats: { pass: 1, fail: 1, ai_match: 0, ai_no_match: 0 },
    });
    expect(humanReviewedOf(s)).toBe(2);
    expect(duplicateFakeOf(s)).toBe(0);
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

describe('clusterCountOf', () => {
  it('counts the visit_clusters groupings on a session', () => {
    const s = session({
      visit_clusters: [
        { group_id: 'g1', visit_ids: [111, 112], image_count: 4 },
        { group_id: 'g2', visit_ids: [130, 131, 132], image_count: 7 },
      ],
    });
    expect(clusterCountOf(s)).toBe(2);
  });

  it('defaults to 0 when visit_clusters is absent (sessions predating the field)', () => {
    expect(clusterCountOf(session({}))).toBe(0);
  });
});

describe('showAiStatsOf', () => {
  it('shows AI stats when has_ai_reviewer is set, regardless of tag/label', () => {
    const s = session({
      tag: 'rest',
      has_ai_reviewer: true,
      assessment_stats: { pass: 0, fail: 0, ai_match: 0, ai_no_match: 0 },
    });
    expect(showAiStatsOf(s)).toBe(true);
  });

  it('falls back to real AI stats for sessions predating has_ai_reviewer', () => {
    const s = session({
      assessment_stats: { pass: 0, fail: 0, ai_match: 3, ai_no_match: 1 },
    });
    expect(showAiStatsOf(s)).toBe(true);
  });

  it('is false when there is no reviewer flag and no recorded AI stats', () => {
    const s = session({
      tag: 'muac',
      assessment_stats: { pass: 2, fail: 0, ai_match: 0, ai_no_match: 0 },
    });
    expect(showAiStatsOf(s)).toBe(false);
  });

  it('tolerates a null session', () => {
    expect(showAiStatsOf(null)).toBe(false);
  });
});

describe('aiFlagsSummary', () => {
  it('falls back to the flat "N flagged" form with no per-label breakdown', () => {
    const s = session({
      assessment_stats: { pass: 0, fail: 0, ai_match: 0, ai_no_match: 3 },
    });
    expect(aiFlagsSummary(s)).toBe('3 flagged');
  });

  it('falls back to flat form when exactly one distinct label produced all flags', () => {
    const s = session({
      assessment_stats: {
        pass: 0,
        fail: 0,
        ai_match: 0,
        ai_no_match: 2,
        ai_flags_by_label: { Hyperzoomed: 2 },
      },
    });
    expect(aiFlagsSummary(s)).toBe('2 flagged');
  });

  it('breaks down by classifier when more than one label is present, stripping "(...)"', () => {
    const s = session({
      assessment_stats: {
        pass: 0,
        fail: 0,
        ai_match: 0,
        ai_no_match: 9,
        ai_flags_by_label: {
          Hyperzoomed: 7,
          'MUAC Mismatch (strict tolerance)': 2,
        },
      },
    });
    expect(aiFlagsSummary(s)).toBe('7 Hyperzoomed, 2 MUAC Mismatch');
  });

  it('falls back to flat form when an unlabeled reviewer flag would otherwise vanish from the breakdown', () => {
    // ai_no_match (5) doesn't reconcile with the labeled total (7+2=9) here
    // on purpose -- simulates a session where some no_match assessments came
    // from a reviewer that sets no badge_label, so they're absent from
    // ai_flags_by_label but still counted in ai_no_match.
    const s = session({
      assessment_stats: {
        pass: 0,
        fail: 0,
        ai_match: 0,
        ai_no_match: 12,
        ai_flags_by_label: {
          Hyperzoomed: 7,
          'MUAC Mismatch (strict tolerance)': 2,
        },
      },
    });
    expect(aiFlagsSummary(s)).toBe('12 flagged');
  });
});
