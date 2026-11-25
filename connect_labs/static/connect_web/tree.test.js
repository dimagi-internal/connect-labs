// The question tree on real Formplayer response shapes (connect_web/tests/fixtures, with sources).
import { describe, expect, it } from 'vitest';

import fixtures from '../../connect_web/tests/fixtures/formplayer_responses.json';
import {
  buildPages,
  collectAnswers,
  compareIx,
  datetimeAnswer,
  datetimeInputValue,
  filterChoices,
  findByIx,
  geoValue,
  metaLocation,
  missingRequired,
  numberAnswer,
  pageOf,
  parseIx,
  questions,
  relocate,
  repeatKey,
  selectedOrdinals,
  submitErrors,
  timeInputValue,
  toggleChoice,
  widgetFor,
} from './tree';

const tree = () => JSON.parse(JSON.stringify(fixtures.form.tree));

describe('widgets', () => {
  it('picks the input for each question in the fixture form', () => {
    const byIx = Object.fromEntries(
      questions(tree()).map((q) => [q.ix, widgetFor(q)]),
    );
    expect(byIx).toEqual({
      0: 'label',
      1: 'text',
      2: 'int',
      3: 'decimal',
      4: 'longint',
      5: 'phone',
      6: 'date',
      7: 'time',
      8: 'datetime',
      9: 'select',
      10: 'multiselect',
      11: 'combobox',
      12: 'dropdown',
      '13,0': 'int',
      '13,1': 'int',
      '14,0': 'geo',
      '14,1': 'text',
      '15_0,0': 'text',
      16: 'image',
      17: 'signature',
      18: 'audio',
      19: 'video',
    });
  });

  it('makes a very long plain select searchable', () => {
    const q = {
      type: 'question',
      ix: '1',
      datatype: 'select',
      choices: Array.from({ length: 31 }, (_, i) => `c${i}`),
    };
    expect(widgetFor(q)).toBe('combobox');
  });

  it('marks an unknown binary control unsupported', () => {
    expect(
      widgetFor({ type: 'question', ix: '1', datatype: 'binary', control: 99 }),
    ).toBe('unsupported');
    expect(widgetFor({ type: 'question', ix: '1', datatype: 'mystery' })).toBe(
      'unsupported',
    );
  });
});

describe('form indexes', () => {
  it('parses and orders them', () => {
    expect(parseIx('15_0,0')).toEqual([
      [15, 0],
      [0, -1],
    ]);
    expect(compareIx('2', '10')).toBeLessThan(0);
    expect(compareIx('13', '13,0')).toBeLessThan(0);
    expect(compareIx('15_0,0', '15_1')).toBeLessThan(0);
    expect(compareIx('14,1', '14,1')).toBe(0);
    expect(repeatKey('15_1')).toBe('15');
    expect(repeatKey('3,2_4')).toBe('3,2');
  });
});

describe('pages', () => {
  const pages = buildPages(tree());

  it('gives each question its own screen, a field-list group one screen, a repeat one screen', () => {
    expect(pages.map((p) => [p.key, p.kind])).toEqual([
      ['0', 'question'],
      ['1', 'question'],
      ['2', 'question'],
      ['3', 'question'],
      ['4', 'question'],
      ['5', 'question'],
      ['6', 'question'],
      ['7', 'question'],
      ['8', 'question'],
      ['9', 'question'],
      ['10', 'question'],
      ['11', 'question'],
      ['12', 'question'],
      ['13', 'group'],
      ['14,0', 'question'],
      ['14,1', 'question'],
      ['repeat:15', 'repeat'],
      ['16', 'question'],
      ['17', 'question'],
      ['18', 'question'],
      ['19', 'question'],
    ]);
  });

  it("titles a plain group's questions with the group's caption", () => {
    expect(pages.find((p) => p.key === '14,0').context).toEqual(['Location']);
  });

  it('keeps the repeat instance and its "add another" together', () => {
    const repeat = pages.find((p) => p.kind === 'repeat');
    expect(repeat.nodes.map((n) => n.ix)).toEqual(['15_0', '15_1']);
  });

  it('skips a group with nothing in it', () => {
    expect(
      buildPages([
        { type: 'sub-group', ix: '0', caption: 'Empty', children: [] },
      ]),
    ).toEqual([]);
  });

  it('keeps its place when the tree changes', () => {
    expect(relocate(pages, '9')).toBe(9);
    // Question 7 became irrelevant and left the tree: land on the next one.
    const without7 = buildPages(tree().filter((n) => n.ix !== '7'));
    expect(without7[relocate(without7, '7')].key).toBe('8');
    // Past the end → the last page; nothing → the first.
    expect(relocate(pages, '99')).toBe(pages.length - 1);
    expect(relocate(pages, null)).toBe(0);
    expect(relocate([], '3')).toBe(0);
  });

  it('finds the page a question is on', () => {
    expect(pages[pageOf(pages, '13,1')].key).toBe('13');
    expect(pages[pageOf(pages, '15_0,0')].key).toBe('repeat:15');
    expect(pageOf(pages, 'nope')).toBe(-1);
  });
});

describe('answers', () => {
  it('collects every answer as submit-all takes it, labels as OK', () => {
    const answers = collectAnswers(tree());
    expect(answers['0']).toBe('OK');
    expect(answers['2']).toBe(4);
    expect(answers['10']).toEqual([1, 3]);
    expect(answers['1']).toBeNull();
    expect(answers['15_0,0']).toBe('Ade');
    expect(Object.keys(answers)).toHaveLength(questions(tree()).length);
  });

  it('lists unanswered required questions', () => {
    expect(missingRequired(tree())).toEqual({
      1: 'An answer is required.',
      9: 'An answer is required.',
    });
  });

  it('converts dates and times both ways', () => {
    expect(datetimeInputValue('2026-12-01T09:30:00+01:00')).toBe(
      '2026-12-01T09:30',
    );
    expect(datetimeInputValue(null)).toBe('');
    expect(datetimeAnswer('2026-12-01T09:30')).toBe('2026-12-01T09:30:00');
    expect(datetimeAnswer('')).toBeNull();
    expect(timeInputValue('9:05')).toBe('09:05');
    expect(timeInputValue('14:30:00')).toBe('14:30');
  });

  it('reads numbers strictly', () => {
    expect(numberAnswer('42', 'int')).toBe(42);
    expect(numberAnswer('', 'int')).toBeNull();
    expect(numberAnswer('4.5', 'int')).toBeUndefined();
    expect(numberAnswer('3000000000', 'int')).toBeUndefined();
    expect(numberAnswer('3000000000', 'longint')).toBe(3000000000);
    expect(numberAnswer('-0.25', 'decimal')).toBe(-0.25);
    expect(numberAnswer('1e5', 'decimal')).toBeUndefined();
    expect(numberAnswer('abc', 'decimal')).toBeUndefined();
  });

  it('handles select ordinals', () => {
    expect(selectedOrdinals(2)).toEqual([2]);
    expect(selectedOrdinals([1, 3])).toEqual([1, 3]);
    expect(selectedOrdinals('[1, 3]')).toEqual([1, 3]);
    expect(selectedOrdinals(null)).toEqual([]);
    expect(toggleChoice([1, 3], 2)).toEqual([1, 2, 3]);
    expect(toggleChoice([1, 3], 3)).toEqual([1]);
  });

  it('reads geo answers and builds meta/location', () => {
    expect(geoValue([9.07, 7.49])).toEqual([9.07, 7.49]);
    expect(geoValue('9.07 7.49 0 10')).toEqual([9.07, 7.49]);
    expect(geoValue(null)).toBeNull();
    expect(
      metaLocation({
        latitude: 9.0765123456,
        longitude: 7.4983,
        altitude: null,
        accuracy: 12.345,
      }),
    ).toBe('9.0765123 7.4983 0 12.35');
  });

  it('filters combobox choices by every word typed', () => {
    const choices = fixtures.form.tree.find((q) => q.ix === '11').choices;
    expect(filterChoices(choices, 'ward 1').map((c) => c.ordinal)).toEqual([
      1, 10, 11, 12,
    ]);
    expect(filterChoices(choices, '').length).toBe(12);
  });

  it('turns submit validation errors into messages', () => {
    expect(submitErrors(fixtures.submit_validation_error.errors)).toEqual({
      1: 'An answer is required.',
      '15_0,0': 'This answer is outside the allowed range.',
    });
  });

  it('finds nodes anywhere in the tree', () => {
    expect(findByIx(tree(), '13,1').caption).toBe('Pregnant women');
    expect(findByIx(tree(), 'x')).toBeNull();
  });
});
