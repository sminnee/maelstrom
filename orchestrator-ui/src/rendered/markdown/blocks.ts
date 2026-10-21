import { diffArrays } from 'diff';
import remarkGfm from 'remark-gfm';
import remarkParse from 'remark-parse';
import { unified } from 'unified';
import type { Diffed } from '../gherkin/diffFeature';

const parser = unified().use(remarkParse).use(remarkGfm);

/** YAML front matter: `---` on the first line, to the next `---` line. */
const FRONT_MATTER = /^---\r?\n[\s\S]*?\r?\n---[ \t]*(?:\r?\n|$)/;

/**
 * A document's top-level blocks, each as its own source text: a heading, a
 * paragraph, a whole list, a whole fence. Front matter is one block, as is
 * each HTML node.
 */
export function markdownBlocks(source: string): string[] {
  const front = source.match(FRONT_MATTER)?.[0] ?? '';
  const blocks = topLevel(source.slice(front.length)).map((node) => node.source);
  return front ? [front.trimEnd(), ...blocks] : blocks;
}

/**
 * The link reference and footnote definitions of a document, as source to
 * append to one block, so the block's `[text][ref]` and `[^1]` still resolve.
 * Empty when there are none.
 */
export function markdownDefinitions(source: string | null): string {
  if (source === null) return '';
  const front = source.match(FRONT_MATTER)?.[0] ?? '';
  return topLevel(source.slice(front.length))
    .filter((node) => node.type === 'definition' || node.type === 'footnoteDefinition')
    .map((node) => `\n\n${node.source}`)
    .join('');
}

function topLevel(body: string): { type: string; source: string }[] {
  const out: { type: string; source: string }[] = [];
  for (const node of parser.parse(body).children) {
    const start = node.position?.start.offset;
    const end = node.position?.end.offset;
    if (start !== undefined && end !== undefined)
      out.push({ type: node.type, source: body.slice(start, end) });
  }
  return out;
}

/**
 * The blocks of each side, diffed as a list. A `null` side has no blocks.
 * Within one changed run, each removed block is followed by the added block
 * at its place, so an edited block reads as its old version, then its new.
 */
export function diffBlocks(oldText: string | null, newText: string | null): Diffed<string>[] {
  const olds = oldText === null ? [] : markdownBlocks(oldText);
  const news = newText === null ? [] : markdownBlocks(newText);
  const out: Diffed<string>[] = [];
  let removed: string[] = [];
  let added: string[] = [];
  const flush = () => {
    for (let i = 0; i < Math.max(removed.length, added.length); i++) {
      if (i < removed.length) out.push({ kind: 'remove', item: removed[i]! });
      if (i < added.length) out.push({ kind: 'add', item: added[i]! });
    }
    removed = [];
    added = [];
  };
  for (const part of diffArrays(olds, news)) {
    if (part.removed) removed.push(...part.value);
    else if (part.added) added.push(...part.value);
    else {
      flush();
      for (const item of part.value) out.push({ kind: 'context', item });
    }
  }
  flush();
  return out;
}
