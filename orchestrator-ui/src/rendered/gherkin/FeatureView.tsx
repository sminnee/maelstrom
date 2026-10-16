import { useMemo } from 'react';
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

/**
 * A `.feature` file as its scenarios. Changes only keeps the feature's head
 * and the scenarios that changed. A side that does not parse falls back to
 * the text view, with the parse error above it.
 */
export function FeatureView(props: RenderedProps) {
  const { old, new: next, mode } = props;
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
  return <Feature diff={diff} mode={mode} />;
}

function Feature({ diff, mode }: { diff: FeatureDiff; mode: Mode }) {
  const shown = (c: { change: Change }) => mode !== 'changes' || c.change !== 'unchanged';
  const children = diff.children.filter(shown);
  const { added, changed, removed } = diff.counts;
  return (
    <article className={styles.feature} data-testid="feature-view">
      {mode === 'changes' && (
        <p className={styles.summary}>
          {`Scenarios: ${added} added, ${changed} changed, ${removed} removed`}
        </p>
      )}
      <Tags tags={diff.tags} />
      <h3 className={styles.featureName}>
        <span className={styles.keyword}>{diff.keyword}:</span> {diff.name}
      </h3>
      <Description lines={diff.description} />
      {children.map((child, i) =>
        child.kind === 'rule' ? (
          <Rule key={i} rule={child} mode={mode} />
        ) : (
          <Block key={i} block={child} />
        ),
      )}
      {children.length === 0 && mode === 'changes' && (
        <p className={styles.note}>No scenario changed.</p>
      )}
    </article>
  );
}

function Rule({ rule, mode }: { rule: RuleDiff; mode: Mode }) {
  const children =
    mode === 'changes' ? rule.children.filter((c) => c.change !== 'unchanged') : rule.children;
  return (
    <section className={styles.rule} data-change={rule.change} aria-label={`Rule: ${rule.name}`}>
      <Head head={rule} />
      {children.map((block, i) => (
        <Block key={i} block={block} />
      ))}
    </section>
  );
}

function Block({ block }: { block: BlockDiff }) {
  return (
    <section
      className={styles.block}
      data-change={block.change}
      aria-label={`${block.keyword}: ${block.name}`}
    >
      <Head head={block} />
      {block.steps.map((step, i) => (
        <StepRow key={i} step={step} />
      ))}
      {block.examples.map((examples, i) => (
        <ExamplesTable key={i} examples={examples} />
      ))}
    </section>
  );
}

function Head({ head }: { head: BlockDiff | RuleDiff }) {
  const badge = BADGE[head.change];
  return (
    <>
      <Tags tags={head.tags} />
      <h4 className={styles.name}>
        <span className={styles.keyword}>{head.keyword}:</span>{' '}
        {head.oldName !== null && (
          <>
            <del className={styles.oldName}>{head.oldName}</del>{' '}
          </>
        )}
        {head.name}
        {badge && <span className={styles.badge}>{badge}</span>}
      </h4>
      <Description lines={head.description} />
    </>
  );
}

function StepRow({ step }: { step: Diffed<Step> }) {
  const { keyword, text, table, docString } = step.item;
  return (
    <div className={styles.step} data-kind={step.kind} data-testid="feature-step">
      <span className={styles.sign}>{SIGN[step.kind]}</span>
      <div>
        <span className={styles.keyword}>{keyword.trim()}</span> {text}
        {table && (
          <table className={styles.table}>
            <tbody>
              {table.map((row, i) => (
                <tr key={i}>
                  {row.map((cell, j) => (
                    <td key={j}>{cell}</td>
                  ))}
                </tr>
              ))}
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
      <Tags tags={examples.tags} />
      <div className={styles.examplesName}>
        <span className={styles.keyword}>{examples.keyword}:</span> {examples.name}
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

function Description({ lines }: { lines: Diffed<string>[] }) {
  if (lines.length === 0) return null;
  return (
    <div className={styles.description}>
      {lines.map((line, i) => (
        <div key={i} data-kind={line.kind}>
          {line.item.trim()}
        </div>
      ))}
    </div>
  );
}
