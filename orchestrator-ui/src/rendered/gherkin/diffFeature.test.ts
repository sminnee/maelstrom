import { describe, expect, it } from 'vitest';
import { type BlockDiff, diffFeature, type FeatureDiff, type RuleDiff } from './diffFeature';

const feature = (body: string) => `Feature: Orders\n\n${body}`;

function diffed(oldText: string | null, newText: string | null): FeatureDiff {
  const result = diffFeature(oldText, newText);
  if ('error' in result) throw new Error(result.error);
  return result;
}

/** Each child as `change name`, for a compact view of the match. */
const outline = (d: FeatureDiff) =>
  d.children.map((c) => `${c.change} ${c.oldName ? `${c.oldName} → ` : ''}${c.name}`);

/** A block's steps as signed lines. */
const steps = (b: BlockDiff | RuleDiff) =>
  (b as BlockDiff).steps.map((s) => `${s.kind} ${s.item.keyword}${s.item.text}`);

const PLACE = `  Scenario: Place an order
    Given a cart with 2 items
    When I check out
    Then I see a receipt
`;
const CANCEL = `  Scenario: Cancel an order
    Given a placed order
    When I cancel it
    Then it is refunded
`;

describe('diffFeature', () => {
  it('marks every child unchanged when both sides match', () => {
    const d = diffed(feature(PLACE), feature(PLACE));
    expect(outline(d)).toEqual(['unchanged Place an order']);
    expect(d.counts).toEqual({ added: 0, changed: 0, removed: 0 });
  });

  it('diffs the steps of an edited scenario', () => {
    const edited = PLACE.replace('a cart with 2 items', 'a cart with 3 items');
    const place = diffed(feature(PLACE), feature(edited)).children[0]!;
    expect(place.change).toBe('modified');
    expect(steps(place)).toEqual([
      'remove Given a cart with 2 items',
      'add Given a cart with 3 items',
      'context When I check out',
      'context Then I see a receipt',
    ]);
  });

  it('marks an added and a removed scenario', () => {
    const d = diffed(feature(PLACE), feature(CANCEL.replace('Cancel', 'Return') + '\n' + PLACE));
    expect(outline(d)).toEqual(['added Return an order', 'unchanged Place an order']);
    expect(steps(d.children[0]!)).toEqual([
      'add Given a placed order',
      'add When I cancel it',
      'add Then it is refunded',
    ]);

    const removed = diffed(feature(PLACE + '\n' + CANCEL), feature(PLACE));
    expect(outline(removed)).toEqual(['unchanged Place an order', 'removed Cancel an order']);
    expect(removed.counts).toEqual({ added: 0, changed: 0, removed: 1 });
  });

  it('pairs a removed and an added scenario at one place as a rename', () => {
    const renamed = PLACE.replace('Place an order', 'Buy the cart');
    const d = diffed(feature(PLACE + '\n' + CANCEL), feature(renamed + '\n' + CANCEL));
    expect(outline(d)).toEqual([
      'renamed Place an order → Buy the cart',
      'unchanged Cancel an order',
    ]);
    expect(steps(d.children[0]!).every((s) => s.startsWith('context'))).toBe(true);
    expect(d.counts).toEqual({ added: 0, changed: 1, removed: 0 });
  });

  it('pairs a rename only with a child of the same kind', () => {
    const background = `  Background:\n    Given I am signed in\n\n`;
    const rule = `  Rule: Refunds\n\n${CANCEL}`;
    const d = diffed(
      feature(PLACE + '\n' + CANCEL),
      feature(background + PLACE.replace('Place an order', 'Buy the cart') + '\n' + rule),
    );
    expect(d.children.map((c) => `${c.change} ${c.kind}`)).toEqual([
      'added background',
      'renamed scenario',
      'removed scenario',
      'added rule',
    ]);
    expect(outline(d).slice(1, 3)).toEqual([
      'renamed Place an order → Buy the cart',
      'removed Cancel an order',
    ]);
  });

  it('diffs the example rows of an outline', () => {
    const outlineOf = (rows: string) => `  Scenario Outline: Pay
    Given I pay <amount>

    Examples:
      | amount |
${rows}`;
    const pay = diffed(
      feature(outlineOf('      | 5 |\n      | 10 |\n')),
      feature(outlineOf('      | 5 |\n      | 20 |\n')),
    ).children[0]! as BlockDiff;
    expect(pay.change).toBe('modified');
    expect(pay.examples[0]!.header).toEqual([{ kind: 'context', item: ['amount'] }]);
    expect(pay.examples[0]!.rows).toEqual([
      { kind: 'context', item: ['5'] },
      { kind: 'remove', item: ['10'] },
      { kind: 'add', item: ['20'] },
    ]);
  });

  it('diffs the scenarios inside a rule', () => {
    const rule = (body: string) => `  Rule: Refunds\n\n${body}`;
    const d = diffed(
      feature(rule(CANCEL)),
      feature(rule(CANCEL.replace('it is refunded', 'it is refunded in full'))),
    );
    const refunds = d.children[0] as RuleDiff;
    expect(refunds.kind).toBe('rule');
    expect(refunds.change).toBe('modified');
    expect(refunds.children.map((c) => `${c.change} ${c.name}`)).toEqual([
      'modified Cancel an order',
    ]);
    expect(d.counts).toEqual({ added: 0, changed: 1, removed: 0 });
  });

  it('marks a child modified when only its tags or description move', () => {
    const rule = (tags: string) => `  ${tags}\n  Rule: Refunds\n\n${CANCEL}`;
    const refunds = diffed(feature(rule('@money')), feature(rule('@money @slow')))
      .children[0] as RuleDiff;
    expect(refunds.change).toBe('modified');
    expect(refunds.tags).toEqual([
      { kind: 'context', item: '@money' },
      { kind: 'add', item: '@slow' },
    ]);
    expect(refunds.children.map((c) => `${c.change} ${c.name}`)).toEqual([
      'unchanged Cancel an order',
    ]);

    const described = PLACE.replace('order\n', 'order\n    Only a signed-in user can order.\n\n');
    const d = diffed(feature(PLACE), feature(described));
    expect(outline(d)).toEqual(['modified Place an order']);
    expect(d.counts).toEqual({ added: 0, changed: 1, removed: 0 });
  });

  it('diffs a step data table and a doc string as part of the step', () => {
    const withTable = (cell: string) =>
      feature(`  Scenario: Stock\n    Given these items:\n      | name |\n      | ${cell} |\n`);
    const stock = diffed(withTable('pen'), withTable('ink')).children[0]! as BlockDiff;
    expect(stock.steps.map((s) => [s.kind, s.item.table])).toEqual([
      ['remove', [['name'], ['pen']]],
      ['add', [['name'], ['ink']]],
    ]);

    const withDoc = (text: string) =>
      feature(`  Scenario: Note\n    Given a note:\n      """\n      ${text}\n      """\n`);
    const note = diffed(withDoc('hello'), withDoc('goodbye')).children[0]! as BlockDiff;
    expect(note.steps.map((s) => [s.kind, s.item.docString])).toEqual([
      ['remove', 'hello'],
      ['add', 'goodbye'],
    ]);
  });

  it('reads a missing side as an empty feature', () => {
    const added = diffed(null, feature(PLACE));
    expect(outline(added)).toEqual(['added Place an order']);
    expect(added.name).toBe('Orders');
    expect(added.counts).toEqual({ added: 1, changed: 0, removed: 0 });
    expect(added.change).toBe('added');

    const deleted = diffed(feature(PLACE), null);
    expect(outline(deleted)).toEqual(['removed Place an order']);
    expect(deleted.name).toBe('Orders');
    expect(deleted.change).toBe('removed');
    expect(diffed(feature(PLACE), feature(PLACE)).change).toBe('unchanged');
  });

  it('diffs a description as paragraphs, so a re-wrap is no change and a list keeps its lines', () => {
    const described = (second: string) =>
      `Feature: Orders\n  An order holds a cart\n  and its payment.\n  \n  ${second}\n`;
    const same = diffed(described('- paid\n  - shipped'), described('- paid\n  - shipped'));
    expect(same.description).toEqual([
      { kind: 'context', item: 'An order holds a cart\nand its payment.' },
      { kind: 'context', item: '- paid\n- shipped' },
    ]);

    const rewrapped = diffed(
      `Feature: Orders\n  An order holds\n  a cart and its payment.\n`,
      `Feature: Orders\n  An order holds a cart\n  and its payment.\n`,
    );
    expect(rewrapped.description).toEqual([
      { kind: 'context', item: 'An order holds a cart\nand its payment.' },
    ]);

    const edited = diffed(described('- paid\n  - shipped'), described('- paid\n  - settled'));
    expect(edited.description).toEqual([
      { kind: 'context', item: 'An order holds a cart\nand its payment.' },
      { kind: 'remove', item: '- paid\n- shipped' },
      { kind: 'add', item: '- paid\n- settled' },
    ]);
  });

  it('returns the parse error of a broken side', () => {
    const result = diffFeature(feature(PLACE), 'Given a step before any feature\n');
    expect(result).toEqual({ error: expect.stringMatching(/^\(1:1\)/) });
  });
});
