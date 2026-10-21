import { Fragment, useMemo } from 'react';
import { Markdown, type ResolveSrc } from '../../markdown/Markdown';
import type { DiffRowKind } from '../../ui/diffSign';
import type { Diffed } from '../gherkin/diffFeature';
import type { RenderedProps, Side } from '../plugin';
import styles from '../rendered.module.css';
import { diffBlocks, markdownDefinitions } from './blocks';

/** An ATX heading, or a setext one: a line underlined with `=` or `-`. */
const HEADING = /^ {0,3}#{1,6}(\s|$)|\n {0,3}(=+|-+)[ \t]*$/;

/** A ref with a scheme, a protocol-relative or rooted path, or a fragment. */
const NOT_RELATIVE = /^([a-z][a-z0-9+.-]*:|\/|#)/i;

/**
 * A markdown file as a document. Whole file and Changes only draw it block by
 * block, and a changed block sits on a spine in its change's hue.
 */
export function MarkdownFileView({
  old,
  new: next,
  mode,
  path,
  oldPath,
  truncated,
  sideUrl,
}: RenderedProps) {
  const resolve = useMemo(
    () => ({
      old: resolver(oldPath ?? path, 'old', sideUrl),
      new: resolver(path, 'new', sideUrl),
    }),
    [path, oldPath, sideUrl],
  );
  // A block drawn alone loses the reference links the rest of its side defines.
  const definitions = useMemo(
    () => ({ old: markdownDefinitions(old), new: markdownDefinitions(next) }),
    [old, next],
  );
  const blocks = useMemo(() => (mode === 'final' ? [] : diffBlocks(old, next)), [old, next, mode]);
  if (mode === 'final') {
    if (next === null) return <p className={styles.note}>Deleted file.</p>;
    return (
      <article className={styles.document}>
        <Markdown source={next} resolveSrc={resolve.new} />
      </article>
    );
  }
  // A wholly added or removed file is one tinted document, not a spine per block.
  const whole = truncated ? null : old === null ? 'added' : next === null ? 'removed' : null;
  if (whole)
    return (
      <article className={styles.document} data-change={whole}>
        <Markdown
          source={(whole === 'added' ? next : old) ?? ''}
          resolveSrc={whole === 'added' ? resolve.new : resolve.old}
        />
      </article>
    );
  const runs = mode === 'changes' ? changedRuns(blocks) : [blocks];
  if (runs.length === 0) return <p className={styles.note}>No changes.</p>;
  return (
    <article className={styles.document}>
      {runs.map((run, i) => (
        <Fragment key={i}>
          {i > 0 && <div className={styles.gap} aria-hidden="true" />}
          {run.map((block, j) => (
            <MarkdownBlock
              key={j}
              kind={block.kind}
              source={block.item + definitions[block.kind === 'remove' ? 'old' : 'new']}
              resolveSrc={block.kind === 'remove' ? resolve.old : resolve.new}
            />
          ))}
        </Fragment>
      ))}
    </article>
  );
}

function MarkdownBlock({
  kind,
  source,
  resolveSrc,
}: {
  kind: DiffRowKind;
  source: string;
  resolveSrc: ResolveSrc;
}) {
  return (
    <div className={styles.markdownBlock} data-kind={kind} data-testid="markdown-block">
      <Markdown source={source} resolveSrc={resolveSrc} />
    </div>
  );
}

/**
 * Each run of changed blocks, after the nearest heading above it as context.
 * A heading the previous run already shows is not shown twice.
 */
function changedRuns(blocks: Diffed<string>[]): Diffed<string>[][] {
  const runs: Diffed<string>[][] = [];
  let shownTo = -1;
  for (let i = 0; i < blocks.length; i++) {
    if (blocks[i]!.kind === 'context') continue;
    let end = i;
    while (end + 1 < blocks.length && blocks[end + 1]!.kind !== 'context') end++;
    const run = blocks.slice(i, end + 1);
    for (let k = i - 1; k > shownTo; k--) {
      const block = blocks[k]!;
      if (block.kind === 'context' && HEADING.test(block.item)) {
        run.unshift(block);
        break;
      }
    }
    runs.push(run);
    shownTo = end;
    i = end;
  }
  return runs;
}

/**
 * Resolves a relative image ref against the file's folder, to that file's
 * bytes on one side. Any other ref, and one that climbs out of the worktree,
 * passes through.
 */
function resolver(
  path: string,
  side: Side,
  sideUrl: (path: string, side: Side) => string,
): ResolveSrc {
  return (ref) => {
    if (NOT_RELATIVE.test(ref)) return ref;
    const parts = path.split('/').slice(0, -1);
    for (const part of ref.split(/[?#]/, 1)[0]!.split('/')) {
      if (part === '..') {
        if (parts.length === 0) return ref;
        parts.pop();
      } else if (part && part !== '.') parts.push(decoded(part));
    }
    return sideUrl(parts.join('/'), side);
  };
}

/** A path segment with its percent escapes undone; a malformed escape stays. */
function decoded(part: string): string {
  try {
    return decodeURIComponent(part);
  } catch {
    return part;
  }
}
