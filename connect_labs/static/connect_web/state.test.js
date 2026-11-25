// The player's screen and form reducers, on real Formplayer response shapes.
import { describe, expect, it } from 'vitest';

import fixtures from '../../connect_web/tests/fixtures/formplayer_responses.json';
import {
  currentIndex,
  formProgress,
  formReducer,
  newForm,
  pagesOf,
  screenFromNavigation,
  visibleColumns,
} from './state';

const clone = (x) => JSON.parse(JSON.stringify(x));
const form = () => newForm(clone(fixtures.form), fixtures.form.selections);

describe('which screen a navigation lands on', () => {
  it('a menu', () => {
    const s = screenFromNavigation(clone(fixtures.menu_start), []);
    expect(s.kind).toBe('menu');
    expect(s.commands.map((c) => c.displayText)).toEqual([
      'Module 1: Intro',
      'Module 2: Using nets',
    ]);
    expect(s.notice).toBeNull();
  });

  it('a case list, keeping the search', () => {
    const s = screenFromNavigation(clone(fixtures.case_list), ['0'], 'Amina');
    expect(s.kind).toBe('cases');
    expect(s.search).toBe('Amina');
    expect(s.selections).toEqual(['0']);
  });

  it('a form, taking the selections Formplayer reports', () => {
    const s = screenFromNavigation(clone(fixtures.form), ['0']);
    expect(s.kind).toBe('form');
    expect(s.form.sessionId).toBe(fixtures.form.session_id);
    expect(s.form.selections).toEqual(fixtures.form.selections);
    expect(s.form.langs).toEqual(['en', 'fra']);
    expect(s.form.pageKey).toBe('0');
  });

  it('case search is shown as not supported yet', () => {
    expect(
      screenFromNavigation({ type: 'query', title: 'Search' }, ['0']).kind,
    ).toBe('unsupported');
  });

  it('anything else is an error carrying the notification', () => {
    const s = screenFromNavigation(
      { notification: { message: 'No such menu', error: true } },
      ['9'],
    );
    expect(s).toMatchObject({ kind: 'error', message: 'No such menu' });
  });

  it('shows the case list columns worth showing', () => {
    expect(visibleColumns(fixtures.case_list)).toEqual([0, 1, 3]);
  });
});

describe('inside a form', () => {
  it('steps forward, refusing to pass an unanswered required question', () => {
    let s = form();
    s = formReducer(s, { type: 'next' }); // the label
    expect(s.pageKey).toBe('1');
    s = formReducer(s, { type: 'next' }); // name is required and empty
    expect(s.pageKey).toBe('1');
    expect(s.errors['1']).toBe('An answer is required.');
    s = formReducer(s, {
      type: 'answered',
      ix: '1',
      resp: clone(fixtures.answer_accepted),
    });
    expect(s.errors['1']).toBeUndefined();
    expect(s.tree.find((q) => q.ix === '1').answer).toBe('Amina Yusuf');
  });

  it('keeps its place when an answer changes which questions exist', () => {
    let s = formReducer(form(), { type: 'goto', index: 9 });
    expect(s.pageKey).toBe('9');
    // The accepted answer's tree only has 0, 1 and 9 (the others became irrelevant).
    s = formReducer(s, {
      type: 'answered',
      ix: '9',
      resp: clone(fixtures.answer_accepted),
    });
    expect(pagesOf(s).map((p) => p.key)).toEqual(['0', '1', '9']);
    expect(currentIndex(s)).toBe(2);
  });

  it('shows a constraint error against the question and keeps the tree', () => {
    const before = form();
    const s = formReducer(before, {
      type: 'answered',
      ix: '2',
      resp: clone(fixtures.answer_constraint),
    });
    expect(s.errors['2']).toBe('Must be between 1 and 30');
    expect(s.tree).toBe(before.tree);
  });

  it('drops errors of questions that are gone', () => {
    let s = formReducer(form(), {
      type: 'localError',
      ix: '3',
      message: 'Enter a number.',
    });
    expect(s.errors['3']).toBe('Enter a number.');
    s = formReducer(s, { type: 'tree', resp: clone(fixtures.answer_accepted) });
    expect(s.errors['3']).toBeUndefined();
  });

  it('goes back, and from the review screen back to the last page', () => {
    let s = formReducer(form(), { type: 'goto', index: 2 });
    s = formReducer(s, { type: 'back' });
    expect(s.pageKey).toBe('1');
    s = formReducer(formReducer(s, { type: 'goto', index: 0 }), {
      type: 'back',
    });
    expect(s.pageKey).toBe('0');
    const last = pagesOf(s).length - 1;
    s = formReducer(formReducer(s, { type: 'goto', index: last }), {
      type: 'next',
    });
    expect(s.review).toBe(true);
    expect(formProgress(s)).toBe(1);
    s = formReducer(s, { type: 'back' });
    expect(s.review).toBe(false);
  });

  it('refuses the review screen while required answers are missing, and jumps to the first', () => {
    let s = formReducer(form(), { type: 'goto', index: 15 });
    s = formReducer(s, { type: 'review' });
    expect(s.review).toBe(false);
    expect(s.pageKey).toBe('1');
    expect(Object.keys(s.errors).sort()).toEqual(['1', '9']);
  });

  it('shows submit-all validation errors and goes to the first', () => {
    let s = formReducer(form(), { type: 'goto', index: 20 });
    s = formReducer(s, { type: 'submitting', value: true });
    s = formReducer(s, {
      type: 'submitErrors',
      errors: fixtures.submit_validation_error.errors,
    });
    expect(s.submitting).toBe(false);
    expect(s.pageKey).toBe('1');
    expect(s.errors['15_0,0']).toBe(
      'This answer is outside the allowed range.',
    );
  });

  it('switches language with the tree Formplayer sends back', () => {
    const s = formReducer(form(), {
      type: 'lang',
      lang: 'fra',
      resp: clone(fixtures.answer_accepted),
    });
    expect(s.lang).toBe('fra');
    expect(s.tree).toHaveLength(3);
  });

  it('switches between one-at-a-time and all questions', () => {
    expect(formReducer(form(), { type: 'mode', mode: 'all' }).mode).toBe('all');
  });

  it('opens straight on the review screen for a form with no questions', () => {
    expect(newForm({ session_id: 'abcdef12-0000', tree: [] }, []).review).toBe(
      true,
    );
  });
});
