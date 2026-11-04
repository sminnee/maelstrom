import { describe, expect, it } from 'vitest';
import type { DiffBody } from '../api/worktreeChanges';
import type { DiffHunk, DiffLine, FileDiff, FileExcerpt } from '../protocol/entities';
import { cardRows, type Card, flow, type Pin } from './flow';

const ctx = (n: number, old = n): DiffLine => ({
  kind: 'context',
  text: `line ${n}`,
  oldLine: old,
  newLine: n,
});
const add = (n: number): DiffLine => ({ kind: 'add', text: `new ${n}`, oldLine: null, newLine: n });

/** A hunk that adds one line after `at`: new lines `at`..`at + 1`, old `at`. */
const grows = (at: number, old = at): DiffHunk => ({
  header: `@@ -${old},1 +${at},2 @@`,
  range: { start: at, end: at + 1 },
  lines: [ctx(at, old), add(at + 1)],
});

const file = (path: string, hunks: DiffHunk[], over: Partial<FileDiff> = {}): FileDiff => ({
  path,
  oldPath: null,
  status: 'modified',
  binary: false,
  additions: hunks.length,
  deletions: 0,
  truncated: false,
  shebang: null,
  hunks,
  ...over,
});

const body = (...files: FileDiff[]): DiffBody => ({
  rev: 'branch',
  groups: [{ name: 'Other', collapsed: false, paths: files.map((f) => f.path) }],
  files,
});

const pin = (id: string, path: string, start: number | null, end = start): Pin => ({
  id,
  target: start === null ? { path, range: null } : { path, range: { start, end: end! } },
  from: null,
});

/** The excerpt the server sends for lines `start`..`end` of a file of `total` lines. */
const excerpt = (path: string, start: number, end: number, total = 100): FileExcerpt => ({
  rev: 'branch',
  path,
  total,
  truncated: false,
  span: {
    range: { start, end },
    lines: Array.from({ length: end - start + 1 }, (_, i) => ctx(start + i)),
  },
});

const excerpts = (...pairs: [Pin, FileExcerpt][]) => new Map(pairs.map(([p, e]) => [p.id, e]));

/** Each span of a card as `start-end` and who drew it: `diff`, or the pins' ids. */
const spans = (card: Card) =>
  card.spans.map(
    (s) =>
      `${s.range.start}-${s.range.end} ${s.by.kind === 'diff' ? 'diff' : s.by.pins.map((p) => p.id).join('+')}`,
  );

/** A card's rows as text, each mark a line of its own. */
const drawn = (card: Card) => {
  const { rows, marks } = cardRows(card);
  const out: string[] = [];
  const at = (i: number) =>
    marks
      .filter((m) => m.before === i)
      .map((m) =>
        m.kind === 'gap'
          ? `⋯ ${m.count ?? 'to end'}`
          : m.kind === 'hunk'
            ? m.header
            : `× ${m.pin.id}`,
      );
  rows.forEach((row, i) => {
    out.push(...at(i));
    out.push(`${row.lineNumbers?.old ?? '-'} ${row.lineNumbers?.new ?? '-'} ${row.text}`);
  });
  out.push(...at(rows.length));
  return out;
};

describe('flow', () => {
  it('draws each changed file as a diff card in its group, with no pins', () => {
    const { sections } = flow(body(file('a.py', [grows(4)])), [], new Map());
    expect(sections.map((s) => [s.group?.name, s.cards.map((c) => c.path)])).toEqual([
      ['Other', ['a.py']],
    ]);
    expect(spans(sections[0]!.cards[0]!)).toEqual(['4-5 diff']);
  });

  it('merges a pin on a changed path into its diff card, clipped around the hunks', () => {
    const p = pin('p', 'a.py', 2, 9);
    const { sections } = flow(
      body(file('a.py', [grows(4)])),
      [p],
      excerpts([p, excerpt('a.py', 2, 9)]),
    );
    expect(sections).toHaveLength(1);
    expect(spans(sections[0]!.cards[0]!)).toEqual(['2-3 p', '4-5 diff', '6-9 p']);
  });

  it('gives a pinned line below a hunk its old number, offset by the hunk above', () => {
    // The hunk adds a line, so new line 8 is old line 7.
    const p = pin('p', 'a.py', 8);
    const { sections } = flow(
      body(file('a.py', [grows(4)])),
      [p],
      excerpts([p, excerpt('a.py', 8, 8)]),
    );
    expect(drawn(sections[0]!.cards[0]!)).toEqual([
      '⋯ 3',
      '@@ -4,1 +4,2 @@',
      '4 4 line 4',
      '- 5 new 5',
      '⋯ 2',
      '× p',
      '7 8 line 8',
      '⋯ 92',
    ]);
  });

  it('puts a pin on another path in a pinned card under Pinned', () => {
    const p = pin('p', 'b.py', null);
    const { sections } = flow(
      body(file('a.py', [grows(4)])),
      [p],
      excerpts([p, excerpt('b.py', 1, 3, 3)]),
    );
    expect(sections.map((s) => [s.group?.name ?? 'Pinned', s.cards.map((c) => c.path)])).toEqual([
      ['Other', ['a.py']],
      ['Pinned', ['b.py']],
    ]);
    const card = sections[1]!.cards[0]!;
    expect(card.reason).toEqual({ kind: 'pinned', from: null });
    // One pin: the card's head unpins it, so its rows carry no ×.
    expect(drawn(card)).toEqual(['1 1 line 1', '2 2 line 2', '3 3 line 3']);
  });

  it('draws spans that touch with no gap, and a gap only between spans that do not', () => {
    const near = pin('near', 'a.py', 6, 6);
    const far = pin('far', 'a.py', 9, 9);
    const { sections } = flow(
      body(file('a.py', [grows(4)])),
      [near, far],
      excerpts([near, excerpt('a.py', 6, 6, 9)], [far, excerpt('a.py', 9, 9, 9)]),
    );
    expect(drawn(sections[0]!.cards[0]!)).toEqual([
      '⋯ 3',
      '@@ -4,1 +4,2 @@',
      '4 4 line 4',
      '- 5 new 5',
      '× near',
      '5 6 line 6',
      '⋯ 2',
      '× far',
      '8 9 line 9',
    ]);
  });

  it('counts the last gap to the end of the file once its length is known, else says to the end', () => {
    const { sections } = flow(body(file('a.py', [grows(4)])), [], new Map());
    expect(drawn(sections[0]!.cards[0]!).at(-1)).toBe('⋯ to end');
  });

  it('draws no gap in an added file, whose hunk is the whole file', () => {
    const added: DiffHunk = {
      header: '@@ -0,0 +1,2 @@',
      range: { start: 1, end: 2 },
      lines: [add(1), add(2)],
    };
    const { sections } = flow(body(file('a.py', [added], { status: 'added' })), [], new Map());
    expect(drawn(sections[0]!.cards[0]!)).toEqual(['@@ -0,0 +1,2 @@', '- 1 new 1', '- 2 new 2']);
  });

  it('draws the lines of two overlapping pins once, and keeps the lines the other covers on unpin', () => {
    const first = pin('first', 'b.py', 1, 3);
    const second = pin('second', 'b.py', 2, 4);
    const both = excerpts([first, excerpt('b.py', 1, 3, 4)], [second, excerpt('b.py', 2, 4, 4)]);
    const card = flow(body(), [first, second], both).sections[0]!.cards[0]!;
    expect(spans(card)).toEqual(['1-1 first', '2-3 first+second', '4-4 second']);
    expect(drawn(card)).toEqual([
      '× first',
      '1 1 line 1',
      '× second',
      '2 2 line 2',
      '3 3 line 3',
      '4 4 line 4',
    ]);

    const left = flow(body(), [second], both).sections[0]!.cards[0]!;
    expect(spans(left)).toEqual(['2-4 second']);
  });

  it('puts a pinned card after the card its pin came from', () => {
    const p: Pin = {
      ...pin('p', 'c.py', null),
      from: { at: { path: 'a.py', range: { start: 4, end: 4 } }, name: 'run' },
    };
    const { sections } = flow(
      body(file('a.py', [grows(4)]), file('b.py', [grows(4)])),
      [p],
      new Map(),
    );
    expect(sections.map((s) => [s.group?.name ?? 'Pinned', s.cards.map((c) => c.path)])).toEqual([
      ['Other', ['a.py', 'c.py', 'b.py']],
    ]);
  });

  it('gives a pin with no line to draw a × after the last row', () => {
    // The gap ran to an end of file not known yet; the file ends at the hunk.
    const p = pin('p', 'a.py', 6, Number.MAX_SAFE_INTEGER);
    const empty = { ...excerpt('a.py', 6, 5, 5), span: { range: { start: 6, end: 5 }, lines: [] } };
    const card = flow(body(file('a.py', [grows(4)])), [p], excerpts([p, empty])).sections[0]!
      .cards[0]!;
    expect(drawn(card)).toEqual(['⋯ 3', '@@ -4,1 +4,2 @@', '4 4 line 4', '- 5 new 5', '× p']);
  });

  it('draws no pinned lines in a deleted file, which has no new side', () => {
    const gone = file('a.py', [], { status: 'deleted' });
    const p = pin('p', 'a.py', 1, 2);
    const card = flow(body(gone), [p], excerpts([p, excerpt('a.py', 1, 2)])).sections[0]!.cards[0]!;
    expect(card.spans).toEqual([]);
  });

  it('marks a card cut when an excerpt of it passed the line cap, and draws no gap past the cap', () => {
    const p = pin('p', 'b.py', null);
    const cut = { ...excerpt('b.py', 1, 2, 9000), truncated: true };
    const card = flow(body(), [p], excerpts([p, cut])).sections[0]!.cards[0]!;
    expect(card.truncated).toBe(true);
    expect(drawn(card)).toEqual(['1 1 line 1', '2 2 line 2']);
    expect(flow(body(), [p], new Map()).sections[0]!.cards[0]!.truncated).toBe(false);

    const long = file('a.py', [grows(4)], { truncated: true });
    expect(drawn(flow(body(long), [], new Map()).sections[0]!.cards[0]!).at(-1)).toBe('- 5 new 5');
  });

  it('keeps a pinned card in place while its lines are read', () => {
    const p = pin('p', 'b.py', null);
    const card = flow(body(), [p], new Map()).sections[0]!.cards[0]!;
    expect([card.path, card.spans, card.pins]).toEqual(['b.py', [], [p]]);
  });
});
