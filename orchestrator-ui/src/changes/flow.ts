import type { DiffBody } from '../api/worktreeChanges';
import type {
  DiffHunk,
  FileDiff,
  FileExcerpt,
  FileGroup,
  FileRange,
  LineRange,
  LineSpan,
} from '../protocol/entities';
import type { DiffRowData } from '../ui/DiffRow';

/** Where a followed target came from. */
export interface Origin {
  at: FileRange;
  name: string;
}

/** Extra context the reviewer brought into view. A `null` range is the whole file. */
export interface Pin {
  id: string;
  target: FileRange | { path: string; range: null };
  from: Origin | null;
}

/** A wire span, tagged with why it is drawn. A pinned span lists every pin that covers it. */
export interface Span extends LineSpan {
  by: { kind: 'diff'; header: string } | { kind: 'pins'; pins: Pin[] };
}

export type Reason = { kind: 'diff'; file: FileDiff } | { kind: 'pinned'; from: Origin | null };

/**
 * One file of the flow, drawn as its spans in line order. `pins` are every pin
 * on the path, read or not; `total` is the file's line count once an excerpt
 * of it is read. `truncated`: an excerpt passed the line cap. `bare` are the
 * pins read with no line to draw: their lines are all hunk lines, or past the end.
 */
export interface Card {
  path: string;
  reason: Reason;
  spans: Span[];
  pins: Pin[];
  total: number | null;
  truncated: boolean;
  bare: Pin[];
}

/** A section of the flow: a **Diff group**, or the pinned cards (`group: null`). */
export interface Section {
  group: FileGroup | null;
  cards: Card[];
}

export interface Flow {
  sections: Section[];
}

/**
 * The flow of one rev: the diff cards in their groups, each pin on a changed
 * path merged into its diff card, and the other pins as pinned cards. A pinned
 * card goes after the card its pin came from, else under Pinned. `excerpts`
 * is keyed by pin id; a pin not read yet draws no lines.
 */
export function flow(diff: DiffBody, pins: Pin[], excerpts: Map<string, FileExcerpt>): Flow {
  const byPath = new Map<string, Pin[]>();
  for (const p of pins) {
    const list = byPath.get(p.target.path) ?? [];
    byPath.set(p.target.path, list);
    list.push(p);
  }
  const files = new Map(diff.files.map((f) => [f.path, f]));
  const sections: Section[] = diff.groups.map((group) => ({
    group,
    cards: group.paths.flatMap((path) => {
      const file = files.get(path);
      return file ? [diffCard(file, byPath.get(path) ?? [], excerpts)] : [];
    }),
  }));
  const pinned: Card[] = [];
  // Map order is the order each path was first pinned, so a card sits where its earliest pin put it.
  for (const [path, list] of byPath) {
    if (files.has(path)) continue;
    const card = pinnedCard(path, list, excerpts);
    const from = list[0]!.from;
    const after = from && findCard([...sections, { group: null, cards: pinned }], from.at.path);
    if (after) after.cards.splice(after.cards.indexOf(after.card) + 1, 0, card);
    else pinned.push(card);
  }
  if (pinned.length > 0) sections.push({ group: null, cards: pinned });
  return { sections: sections.filter((s) => s.cards.length > 0) };
}

function findCard(sections: Section[], path: string) {
  for (const section of sections) {
    const card = section.cards.find((c) => c.path === path);
    if (card) return { cards: section.cards, card };
  }
  return null;
}

function diffCard(file: FileDiff, pins: Pin[], excerpts: Map<string, FileExcerpt>): Card {
  const hunks: Span[] = file.hunks.map((h) => ({
    range: h.range,
    lines: h.lines,
    by: { kind: 'diff', header: h.header },
  }));
  const extra = file.status === 'deleted' ? [] : pinnedSpans(pins, excerpts, file.hunks);
  return {
    path: file.path,
    reason: { kind: 'diff', file },
    spans: [...hunks, ...extra].sort(
      // An empty hunk at a line sits before a pinned line there: its removed lines came first.
      (a, b) => a.range.start - b.range.start || (a.by.kind === 'diff' ? -1 : 1),
    ),
    pins,
    total: totalOf(pins, excerpts),
    truncated: cut(pins, excerpts),
    bare: bareOf(pins, excerpts, extra),
  };
}

function pinnedCard(path: string, pins: Pin[], excerpts: Map<string, FileExcerpt>): Card {
  const spans = pinnedSpans(pins, excerpts, []);
  return {
    path,
    reason: { kind: 'pinned', from: pins[0]!.from },
    spans,
    pins,
    total: totalOf(pins, excerpts),
    truncated: cut(pins, excerpts),
    bare: bareOf(pins, excerpts, spans),
  };
}

const bareOf = (pins: Pin[], excerpts: Map<string, FileExcerpt>, spans: Span[]) =>
  pins.filter(
    (p) => excerpts.has(p.id) && !spans.some((s) => s.by.kind === 'pins' && s.by.pins.includes(p)),
  );

const cut = (pins: Pin[], excerpts: Map<string, FileExcerpt>) =>
  pins.some((p) => excerpts.get(p.id)?.truncated);

function totalOf(pins: Pin[], excerpts: Map<string, FileExcerpt>): number | null {
  for (const p of pins) {
    const read = excerpts.get(p.id);
    if (read) return read.total;
  }
  return null;
}

/**
 * The union of the pins' lines less the hunks' lines, cut where the pins that
 * cover a line change. Each line takes the old number the hunk above it leaves.
 */
function pinnedSpans(pins: Pin[], excerpts: Map<string, FileExcerpt>, hunks: DiffHunk[]): Span[] {
  const cover = new Map<number, { text: string; pins: Pin[] }>();
  for (const p of pins) {
    for (const line of excerpts.get(p.id)?.span.lines ?? []) {
      const n = line.newLine!;
      if (hunks.some((h) => n >= h.range.start && n <= h.range.end)) continue;
      const entry = cover.get(n) ?? { text: line.text, pins: [] };
      cover.set(n, entry);
      entry.pins.push(p);
    }
  }
  const out: Span[] = [];
  let open: { range: LineRange; lines: Span['lines']; pins: Pin[] } | null = null;
  for (const n of [...cover.keys()].sort((a, b) => a - b)) {
    const { text, pins: over } = cover.get(n)!;
    const same =
      open &&
      open.range.end === n - 1 &&
      open.pins.length === over.length &&
      open.pins.every((p, i) => p === over[i]);
    if (!same) {
      if (open)
        out.push({ range: open.range, lines: open.lines, by: { kind: 'pins', pins: open.pins } });
      open = { range: { start: n, end: n }, lines: [], pins: over };
    }
    open!.range.end = n;
    open!.lines.push({ kind: 'context', text, oldLine: n + shift(hunks, n), newLine: n });
  }
  if (open)
    out.push({ range: open.range, lines: open.lines, by: { kind: 'pins', pins: open.pins } });
  return out;
}

const OLD_START = /^@@ -(\d+)(?:,(\d+))?/;

/** Old minus new line numbers below the last hunk above `n`: 0 above the first. */
function shift(hunks: DiffHunk[], n: number): number {
  const above = hunks.filter((h) => h.range.end < n).at(-1);
  const found = above && OLD_START.exec(above.header);
  if (!found) return 0;
  const start = Number(found[1]);
  const count = found[2] === undefined ? 1 : Number(found[2]);
  // An empty side names the line before it, as the server's range does.
  const oldNext = count === 0 ? start + 1 : start + count;
  return oldNext - (above.range.end + 1);
}

/** What draws between rows: a hunk's header, a run of hidden lines, or a pin's ×. */
export type Mark =
  | { kind: 'hunk'; before: number; header: string }
  /** `count` is `null` when the hidden lines run to an end of file not known yet. */
  | { kind: 'gap'; before: number; start: number; count: number | null }
  | { kind: 'pin'; before: number; pin: Pin };

/** A card's rows, and the marks drawn before them. A mark `before: rows.length` is after the last. */
export interface CardRows {
  rows: DiffRowData[];
  marks: Mark[];
}

/**
 * A card's spans as rows, with a gap mark between spans that do not touch. A
 * selection counts in `rows`, which hold no marks. Each pin gets a × at its
 * first row, or after the last row when it draws none, except the one pin of a
 * pinned card, whose head unpins it. A card past the line cap has no gap at its end.
 */
export function cardRows(card: Card): CardRows {
  const rows: DiffRowData[] = [];
  const marks: Mark[] = [];
  const file = card.reason.kind === 'diff' ? card.reason.file : null;
  // An added file's hunk is the whole file; a deleted file has no new side.
  const gaps = !file || file.status === 'modified' || file.status === 'renamed';
  const crossed = card.reason.kind === 'diff' || card.pins.length > 1;
  const marked = new Set<string>();
  let next = 1;
  for (const span of card.spans) {
    if (gaps && span.range.start > next)
      marks.push({ kind: 'gap', before: rows.length, start: next, count: span.range.start - next });
    if (span.by.kind === 'diff')
      marks.push({ kind: 'hunk', before: rows.length, header: span.by.header });
    else if (crossed)
      for (const p of span.by.pins) {
        if (marked.has(p.id)) continue;
        marked.add(p.id);
        marks.push({ kind: 'pin', before: rows.length, pin: p });
      }
    for (const line of span.lines)
      rows.push({
        kind: line.kind,
        text: line.text,
        lineNumbers: { old: line.oldLine, new: line.newLine },
      });
    next = Math.max(next, span.range.end + 1);
  }
  if (crossed) for (const p of card.bare) marks.push({ kind: 'pin', before: rows.length, pin: p });
  const atEnd = card.total === null ? null : card.total - next + 1;
  const cut = file?.truncated || card.truncated;
  if (gaps && card.spans.length > 0 && !cut && (atEnd === null || atEnd > 0))
    marks.push({ kind: 'gap', before: rows.length, start: next, count: atEnd });
  return { rows, marks };
}
