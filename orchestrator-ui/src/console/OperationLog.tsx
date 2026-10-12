import { useState } from 'react';
import { Link } from 'react-router';
import { useOperationLog } from '../api/operations';
import { useHrefFor, useLinkState } from '../nav/useNav';
import type { Operation, OperationStep } from '../protocol/entities';
import { operationLine, operationTarget } from '../selectors/operations';
import { clockTime, durationOf } from '../protocol/time';
import { actionIcon, stepIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import { NextChains } from '../worktrees/NextChains';
import { useTick } from './useTick';
import styles from './OperationLog.module.css';

/**
 * One operation's log: a head that says what it is and where, then a block per
 * **Step**. A done step folds to one line; the step that runs, refused, or
 * failed opens. A refused close offers the closes that do not refuse.
 */
export function OperationLog({
  op,
  onTarget,
  onRetry,
  retryError,
}: {
  op: Operation;
  onTarget: () => void;
  onRetry?: () => Promise<unknown>;
  /** Why the last retry of this operation failed, if it did. */
  retryError?: Error | null;
}) {
  const now = useTick(op.state === 'running');
  const { data } = useOperationLog(op.id);
  const linesOf = (name: string) => data?.steps.find((s) => s.name === name)?.lines ?? [];
  return (
    <section className={styles.log} aria-label="Operation log" data-state={op.state}>
      <header className={styles.head}>
        <h2 className={`${styles.title} wrap`}>{operationLine(op)}</h2>
        <div className={styles.meta}>
          <TargetLink op={op} onTarget={onTarget} />
          <span>
            started{' '}
            <time dateTime={op.startedAt} className="nowrap">
              {clockTime(op.startedAt, now)}
            </time>
          </span>
          <span className="nowrap">{durationOf(op, now)}</span>
          {onRetry && (
            <AppButton
              variant="quiet"
              icon={actionIcon('retry')}
              onClick={onRetry}
              title="Retry (R)"
            >
              Retry
            </AppButton>
          )}
        </div>
        {retryError && (
          <p className={styles.retryError} role="alert">
            Retry failed: {retryError.message}
          </p>
        )}
      </header>
      <ol className={styles.steps}>
        {op.steps.map((step) => (
          <StepBlock key={step.name} step={step} lines={linesOf(step.name)} now={now} />
        ))}
      </ol>
      {op.state === 'refused' && op.kind === 'close' && op.worktreeId !== null && (
        <NextChains worktreeId={op.worktreeId} />
      )}
    </section>
  );
}

/** What the operation acted on, as a link to its card when it has one. */
function TargetLink({ op, onTarget }: { op: Operation; onTarget: () => void }) {
  const { label, card } = operationTarget(op);
  if (!card) return <span className={`${styles.target} truncate`}>{label}</span>;
  return <CardLink card={card} label={label} onTarget={onTarget} />;
}

function CardLink({
  card,
  label,
  onTarget,
}: {
  card: NonNullable<ReturnType<typeof operationTarget>['card']>;
  label: string;
  onTarget: () => void;
}) {
  const to = useHrefFor({ card });
  const linkState = useLinkState();
  return (
    <Link
      to={to}
      state={linkState}
      className={`${styles.target} truncate`}
      onClick={(e) => {
        if (e.metaKey || e.ctrlKey || e.altKey || e.shiftKey || e.button !== 0) return;
        e.preventDefault();
        onTarget();
      }}
    >
      {label}
    </Link>
  );
}

/** Whether a step opens by itself: the one that runs, refused, or failed. */
const opensItself = (step: OperationStep) =>
  step.state === 'running' || step.state === 'refused' || step.state === 'failed';

function StepBlock({ step, lines, now }: { step: OperationStep; lines: string[]; now: number }) {
  // The operator's own toggle wins; until then the step's state decides.
  const [toggled, setToggled] = useState<boolean | null>(null);
  const open = toggled ?? opensItself(step);
  const glyph = step.state === 'pending' ? 'pending' : step.state;
  return (
    <li className={styles.step} data-state={step.state}>
      <button
        type="button"
        className={styles.stepHead}
        aria-expanded={open}
        onClick={() => setToggled(!open)}
      >
        <span className={styles.glyph}>{stepIcon(glyph)}</span>
        <span className={`${styles.name} truncate`}>{step.name}</span>
        {step.words && <span className={`${styles.words} truncate`}>{step.words}</span>}
        <span className={`${styles.duration} nowrap`}>{durationOf(step, now)}</span>
      </button>
      {open && lines.length > 0 && <pre className={styles.lines}>{lines.join('\n')}</pre>}
    </li>
  );
}
