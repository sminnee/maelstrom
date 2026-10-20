import { Fragment, type MouseEvent, useId, useMemo } from 'react';
import { Markdown } from '../../markdown/Markdown';
import { SIGN } from '../../ui/diffSign';
import type { Mode, RenderedProps } from '../plugin';
import { TextView } from '../TextView';
import styles from '../rendered.module.css';
import {
  type BlockDiff,
  type Change,
  type Diffed,
  diffFeature,
  type ExamplesDiff,
  type FeatureDiff,
  type RuleDiff,
} from './diffFeature';
import type { Step } from './parse';

const BADGE: Record<Change, string | null> = {
  added: 'Added',
  removed: 'Removed',
  modified: 'Changed',
  renamed: 'Renamed',
  unchanged: null,
};

/** A change's mark: alone in the contents, before the label on a badge. */
const MARK: Record<Change, string | null> = {
  added: '+',
  removed: '−',
  modified: '•',
  renamed: '→',
  unchanged: null,
};

/**
 * A `.feature` file as its scenarios. Changes only keeps the feature's head
 * and the scenarios that changed. A side that does not parse falls back to
 * the text view, with the parse error above it.
 */
export function FeatureView(props: RenderedProps) {
  const { old, new: next, mode, truncated } = props;
  const diff = useMemo(
    () => (mode === 'final' ? diffFeature(next, next) : diffFeature(old, next)),
    [old, next, mode],
  );
  if ('error' in diff)
    return (
      <>
        <p className={styles.note} role="status">
          Not valid Gherkin, so shown as text: {diff.error}
        </p>
        <TextView {...props} />
      </>
    );
  if (mode === 'final' && next === null) return <p className={styles.note}>Deleted file.</p>;
  // An unread side is not a new or deleted file.
  const whole = truncated ? { ...diff, change: 'unchanged' as const } : diff;
  return <Feature diff={whole} mode={mode} />;
}

/** A rule or a scenario the mode shows, with the id of its heading. */
interface Entry<T> {
  id: string;
  child: T;
}
type Outline = (Entry<BlockDiff> | (Entry<RuleDiff> & { children: Entry<BlockDiff>[] }))[];

/**
 * The children the mode shows. Ids come from each child's place in the whole
 * file, not its name: two scenarios can share a name.
 */
function outline(diff: FeatureDiff, mode: Mode, prefix: string): Outline {
  const shown = (c: { change: Change }) => mode !== 'changes' || c.change !== 'unchanged';
  const out: Outline = [];
  diff.children.forEach((child, i) => {
    if (!shown(child)) return;
    const id = `${prefix}-${i}`;
    if (child.kind !== 'rule') {
      out.push({ id, child });
      return;
    }
    const children = child.children.flatMap((block, j) =>
      shown(block) ? [{ id: `${id}-${j}`, child: block }] : [],
    );
    out.push({ id, child, children });
  });
  return out;
}

function Feature({ diff, mode }: { diff: FeatureDiff; mode: Mode }) {
  const children = outline(diff, mode, `feature${useId()}`);
  const { added, changed, removed } = diff.counts;
  return (
    <div className={styles.featureView}>
      <div className={styles.featureLayout}>
        <Contents entries={children} />
        <article className={styles.feature} data-change={diff.change} data-testid="feature-view">
          {mode === 'changes' && (
            <p className={styles.summary}>
              {`Scenarios: ${added} added, ${changed} changed, ${removed} removed`}
            </p>
          )}
          <div className={`${styles.head} ${styles.featureHead}`}>
            <h3 className={styles.featureName}>
              <KindMark type="feature" change={diff.change} keyword={diff.keyword} />
              {diff.name}
            </h3>
            <Tags tags={diff.tags} />
          </div>
          <Description paragraphs={diff.description} />
          {children.map((entry, i) =>
            'children' in entry ? (
              <Fragment key={entry.id}>
                {i > 0 && <hr className={styles.ruleBreak} />}
                <Rule id={entry.id} rule={entry.child} blocks={entry.children} />
              </Fragment>
            ) : (
              <Block key={entry.id} id={entry.id} block={entry.child} />
            ),
          )}
          {children.length === 0 && mode === 'changes' && (
            <p className={styles.note}>No scenario changed.</p>
          )}
        </article>
      </div>
    </div>
  );
}

/** A link per rule and scenario shown, the scenarios under their rule. */
function Contents({ entries }: { entries: Outline }) {
  if (entries.length === 0) return null;
  const link = (entry: Entry<BlockDiff | RuleDiff>) => <ContentsLink entry={entry} />;
  return (
    <nav className={styles.contents} aria-label="Contents">
      <ul>
        {entries.map((entry) => (
          <li key={entry.id}>
            {link(entry)}
            {'children' in entry && entry.children.length > 0 && (
              <ul>
                {entry.children.map((block) => (
                  <li key={block.id}>{link(block)}</li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </nav>
  );
}

function ContentsLink({ entry: { id, child } }: { entry: Entry<BlockDiff | RuleDiff> }) {
  // The app routes on the URL, so the link scrolls its heading into view
  // rather than move the hash.
  const jump = (event: MouseEvent) => {
    event.preventDefault();
    document.getElementById(id)?.scrollIntoView({ block: 'start' });
  };
  return (
    <>
      {/* The mark is the change's only sign here, so it carries a name. */}
      <span
        className={styles.mark}
        data-change={child.change}
        role={BADGE[child.change] ? 'img' : undefined}
        aria-label={BADGE[child.change] ?? undefined}
      >
        {MARK[child.change]}
      </span>
      <a href={`#${id}`} onClick={jump}>
        {child.name || child.keyword}
      </a>
    </>
  );
}

function Rule({ id, rule, blocks }: { id: string; rule: RuleDiff; blocks: Entry<BlockDiff>[] }) {
  return (
    <section
      className={styles.section}
      data-change={rule.change}
      aria-label={`${rule.keyword}: ${rule.name}`}
    >
      <Head id={id} head={rule} level="h4" className={styles.ruleName} />
      {blocks.map((entry) => (
        <Block key={entry.id} id={entry.id} block={entry.child} level="h5" />
      ))}
    </section>
  );
}

/** A scenario's heading is one level under its rule's, or under the feature's. */
function Block({ id, block, level = 'h4' }: { id: string; block: BlockDiff; level?: 'h4' | 'h5' }) {
  return (
    <section
      className={`${styles.section} ${styles.block}`}
      data-change={block.change}
      aria-label={`${block.keyword}: ${block.name}`}
    >
      <Head id={id} head={block} level={level} className={styles.blockName} />
      {block.steps.length > 0 && (
        <div className={styles.steps}>
          {block.steps.map((step, i) => (
            <StepRow key={i} step={step} />
          ))}
        </div>
      )}
      {block.examples.map((examples, i) => (
        <ExamplesTable key={i} examples={examples} />
      ))}
    </section>
  );
}

function Head({
  id,
  head,
  level: Heading,
  className,
}: {
  id: string;
  head: BlockDiff | RuleDiff;
  level: 'h4' | 'h5';
  className: string | undefined;
}) {
  const badge = BADGE[head.change];
  const headClass = head.kind === 'rule' ? styles.ruleHead : styles.blockHead;
  return (
    <>
      <div className={`${styles.head} ${headClass}`}>
        <Heading id={id} className={className}>
          <KindMark type={head.kind} change={head.change} keyword={head.keyword} />
          {head.oldName !== null && (
            <>
              <del className={styles.oldName}>{head.oldName}</del>{' '}
            </>
          )}
          {head.name || <span className={styles.keyword}>{head.keyword}</span>}
          {badge && (
            <span className={styles.badge} data-change={head.change}>
              <span aria-hidden>{MARK[head.change]}</span> {badge}
            </span>
          )}
        </Heading>
        <Tags tags={head.tags} />
      </div>
      <Description paragraphs={head.description} />
    </>
  );
}

function StepRow({ step }: { step: Diffed<Step> }) {
  const { keyword, text, table, docString } = step.item;
  return (
    <div className={styles.step} data-kind={step.kind} data-testid="feature-step">
      <span className={styles.sign}>{SIGN[step.kind]}</span>
      <div>
        <span className={`${styles.keyword} ${styles.stepKeyword}`}>{keyword.trim()}</span> {text}
        {table && (
          <table className={styles.table}>
            <tbody>
              {table.map((row, i) => {
                // A step table's first row names its columns, as an outline's header does.
                const Cell = i === 0 ? 'th' : 'td';
                return (
                  <tr key={i}>
                    {row.map((cell, j) => (
                      <Cell key={j}>{cell}</Cell>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {docString !== null && <pre className={styles.docString}>{docString}</pre>}
      </div>
    </div>
  );
}

function ExamplesTable({ examples }: { examples: ExamplesDiff }) {
  return (
    <div className={styles.examples}>
      <div className={styles.head}>
        <div className={styles.examplesName}>
          <span className={styles.keyword}>{examples.keyword}:</span> {examples.name}
        </div>
        <Tags tags={examples.tags} />
      </div>
      <table className={styles.table}>
        <thead>
          {examples.header.map((row, i) => (
            <TableRow key={i} row={row} cell="th" />
          ))}
        </thead>
        <tbody>
          {examples.rows.map((row, i) => (
            <TableRow key={i} row={row} cell="td" />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TableRow({ row, cell: Cell }: { row: Diffed<string[]>; cell: 'th' | 'td' }) {
  return (
    <tr data-kind={row.kind}>
      <td className={styles.sign}>{SIGN[row.kind]}</td>
      {row.item.map((value, i) => (
        <Cell key={i}>{value}</Cell>
      ))}
    </tr>
  );
}

/** The letter each keyword shows as. The keyword stays the accessible name. */
const KIND_LETTER = { feature: 'F', rule: 'R', background: 'B', scenario: 'S' } as const;

function KindMark({
  type,
  change,
  keyword,
}: {
  type: keyof typeof KIND_LETTER;
  change: Change | FeatureDiff['change'];
  keyword: string;
}) {
  return (
    <>
      <span
        className={styles.kindMark}
        data-type={type}
        data-change={change}
        title={keyword}
        aria-hidden
      >
        {KIND_LETTER[type]}
      </span>
      <span className="srOnly">{keyword}:</span>{' '}
    </>
  );
}

function Tags({ tags }: { tags: Diffed<string>[] }) {
  if (tags.length === 0) return null;
  return (
    <div className={styles.tags}>
      {tags.map((tag, i) => (
        <span key={i} className={styles.tag} data-kind={tag.kind}>
          {tag.item}
        </span>
      ))}
    </div>
  );
}

/** Each paragraph as markdown, so a backticked name reads as a literal. */
function Description({ paragraphs }: { paragraphs: Diffed<string>[] }) {
  if (paragraphs.length === 0) return null;
  return (
    <div className={styles.description}>
      {paragraphs.map((paragraph, i) => (
        <div key={i} data-kind={paragraph.kind}>
          <Markdown source={paragraph.item} />
        </div>
      ))}
    </div>
  );
}
