import { diffArrays } from 'diff';
import type { DiffRowKind } from '../../ui/diffSign';
import {
  type Block,
  type Examples,
  type Feature,
  parseFeature,
  type Rule,
  type Step,
} from './parse';

/** What happened to a scenario, a background or a rule between the two sides. */
export type Change = 'added' | 'removed' | 'modified' | 'renamed' | 'unchanged';

/** One item of a list diffed as a whole: a step, a tag, a table row, a description paragraph. */
export interface Diffed<T> {
  kind: DiffRowKind;
  item: T;
}

interface Head {
  change: Change;
  keyword: string;
  name: string;
  /** The old name of a renamed child, else `null`. */
  oldName: string | null;
  description: Diffed<string>[];
  tags: Diffed<string>[];
}

export interface ExamplesDiff {
  keyword: string;
  name: string;
  tags: Diffed<string>[];
  header: Diffed<string[]>[];
  rows: Diffed<string[]>[];
}

export interface BlockDiff extends Head {
  kind: Block['kind'];
  steps: Diffed<Step>[];
  examples: ExamplesDiff[];
}

export interface RuleDiff extends Head {
  kind: 'rule';
  children: BlockDiff[];
}

export interface FeatureDiff {
  /** `added` or `removed` when one side is `null`: the whole file is new or deleted. */
  change: 'added' | 'removed' | 'unchanged';
  keyword: string;
  name: string;
  description: Diffed<string>[];
  tags: Diffed<string>[];
  children: (BlockDiff | RuleDiff)[];
  /** Scenarios, inside rules too. A renamed one counts as changed. */
  counts: { added: number; changed: number; removed: number };
}

/**
 * Two versions of a `.feature` file as one diff of its scenarios. A missing
 * side reads as a feature with no children. A side that does not parse makes
 * the whole result its `{ error }`.
 */
export function diffFeature(
  oldText: string | null,
  newText: string | null,
): FeatureDiff | { error: string } {
  const sides: (Feature | null)[] = [];
  for (const text of [oldText, newText]) {
    if (text === null) {
      sides.push(null);
      continue;
    }
    const parsed = parseFeature(text);
    if ('error' in parsed) return parsed;
    sides.push(parsed.feature);
  }
  const [a, b] = sides;
  const shown = b ?? a;
  const children = matchChildren(a?.children ?? [], b?.children ?? [], diffChild);
  const counts = { added: 0, changed: 0, removed: 0 };
  for (const child of children.flatMap((c) => (c.kind === 'rule' ? c.children : [c]))) {
    if (child.kind !== 'scenario') continue;
    if (child.change === 'added') counts.added++;
    else if (child.change === 'removed') counts.removed++;
    else if (child.change !== 'unchanged') counts.changed++;
  }
  return {
    change: oldText === null ? 'added' : newText === null ? 'removed' : 'unchanged',
    keyword: shown?.keyword ?? 'Feature',
    name: shown?.name ?? '',
    description: diffList(paragraphs(a?.description), paragraphs(b?.description), wordsKey),
    tags: diffList(a?.tags ?? [], b?.tags ?? []),
    children,
    counts,
  };
}

/**
 * Pair each old child with a new one by kind and name, in order. A run of
 * removed children next to a run of added ones pairs up by kind, in order, as
 * renames; what is left over is added or removed.
 */
function matchChildren<T extends Block | Rule, D>(
  olds: readonly T[],
  news: readonly T[],
  diff: (a: T | null, b: T | null, renamed: boolean) => D,
): D[] {
  const out: D[] = [];
  let removed: T[] = [];
  let added: T[] = [];
  const flush = () => {
    for (const a of removed) {
      const i = added.findIndex((b) => b.kind === a.kind);
      if (i < 0) {
        out.push(diff(a, null, false));
        continue;
      }
      // Added children before the partner keep their place ahead of it.
      for (const b of added.splice(0, i)) out.push(diff(null, b, false));
      out.push(diff(a, added.shift()!, true));
    }
    for (const b of added) out.push(diff(null, b, false));
    removed = [];
    added = [];
  };
  const key = (c: T) => `${c.kind}\0${c.name}`;
  let i = 0;
  let j = 0;
  for (const part of diffArrays([...olds], [...news], {
    comparator: (x, y) => key(x) === key(y),
  })) {
    const n = part.count;
    if (part.removed) removed.push(...olds.slice(i, (i += n)));
    else if (part.added) added.push(...news.slice(j, (j += n)));
    else {
      flush();
      for (let k = 0; k < n; k++) out.push(diff(olds[i + k]!, news[j + k]!, false));
      i += n;
      j += n;
    }
  }
  flush();
  return out;
}

function diffChild(a: Block | Rule | null, b: Block | Rule | null, renamed: boolean) {
  return (a ?? b)!.kind === 'rule'
    ? diffRule(a as Rule | null, b as Rule | null, renamed)
    : diffBlock(a as Block | null, b as Block | null, renamed);
}

function diffRule(a: Rule | null, b: Rule | null, renamed: boolean): RuleDiff {
  const children = matchChildren(a?.children ?? [], b?.children ?? [], diffBlock);
  const head = diffHead(a, b, renamed);
  return {
    ...head,
    kind: 'rule',
    change: settle(
      head.change,
      a,
      b,
      headMoved(head) || children.some((c) => c.change !== 'unchanged'),
    ),
    children,
  };
}

function diffBlock(a: Block | null, b: Block | null, renamed: boolean): BlockDiff {
  const steps = diffList(a?.steps ?? [], b?.steps ?? [], stepKey);
  const examples = diffExamples(a?.examples ?? [], b?.examples ?? []);
  const head = diffHead(a, b, renamed);
  const edited =
    headMoved(head) ||
    steps.some(changed) ||
    examples.some((e) => [e.tags, e.header, e.rows].some((list) => list.some(changed)));
  return {
    ...head,
    kind: (a ?? b)!.kind as Block['kind'],
    change: settle(head.change, a, b, edited),
    steps,
    examples,
  };
}

/**
 * Examples pair by place: a scenario has few, and they seldom move. One with
 * no partner diffs against an empty table.
 */
function diffExamples(olds: Examples[], news: Examples[]): ExamplesDiff[] {
  return Array.from({ length: Math.max(olds.length, news.length) }, (_, i) => {
    const a = olds[i];
    const b = news[i];
    // One side has an item at every place up to the longer length.
    const shown = (b ?? a)!;
    return {
      keyword: shown.keyword,
      name: shown.name,
      tags: diffList(a?.tags ?? [], b?.tags ?? []),
      header: diffList(a?.header ? [a.header] : [], b?.header ? [b.header] : [], cellsKey),
      rows: diffList(a?.rows ?? [], b?.rows ?? [], cellsKey),
    };
  });
}

/** The head of a child: what its own name, tags and description say. */
function diffHead(a: Block | Rule | null, b: Block | Rule | null, renamed: boolean): Head {
  const shown = (b ?? a)!;
  const description = diffList(paragraphs(a?.description), paragraphs(b?.description), wordsKey);
  const tags = diffList(a?.tags ?? [], b?.tags ?? []);
  return {
    change: !a ? 'added' : !b ? 'removed' : renamed ? 'renamed' : 'unchanged',
    keyword: shown.keyword,
    name: shown.name,
    oldName: renamed ? a!.name : null,
    description,
    tags,
  };
}

const headMoved = (head: Head) => head.tags.some(changed) || head.description.some(changed);

/** A matched child is modified when its head or its body moved. */
function settle(change: Change, a: unknown, b: unknown, edited: boolean): Change {
  if (change !== 'unchanged' || !a || !b) return change;
  return edited ? 'modified' : 'unchanged';
}

function diffList<T>(olds: readonly T[], news: readonly T[], key: (x: T) => string = String) {
  const out: Diffed<T>[] = [];
  let i = 0;
  let j = 0;
  for (const part of diffArrays([...olds], [...news], {
    comparator: (x, y) => key(x) === key(y),
  })) {
    const n = part.count;
    if (part.removed)
      for (const item of olds.slice(i, (i += n))) out.push({ kind: 'remove', item });
    else if (part.added)
      for (const item of news.slice(j, (j += n))) out.push({ kind: 'add', item });
    else {
      for (const item of news.slice(j, j + n)) out.push({ kind: 'context', item });
      i += n;
      j += n;
    }
  }
  return out;
}

const changed = (d: Diffed<unknown>) => d.kind !== 'context';
const stepKey = (s: Step) => JSON.stringify([s.keyword.trim(), s.text, s.table, s.docString]);
const cellsKey = (row: string[]) => JSON.stringify(row);

/**
 * A description as its paragraphs: split on blank lines, each line trimmed
 * of its indent. The lines stay, so a markdown list or table keeps its shape;
 * markdown draws a soft wrap as a space.
 */
const paragraphs = (text: string | undefined) =>
  (text ?? '')
    .split(/\n\s*\n/)
    .map((p) =>
      p
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean)
        .join('\n'),
    )
    .filter(Boolean);
/** A paragraph's words, so a paragraph wrapped again is no change. */
const wordsKey = (paragraph: string) => paragraph.replace(/\s+/g, ' ');
