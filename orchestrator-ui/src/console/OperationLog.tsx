import { useState } from 'react';
import { Link } from 'react-router';
import { useOperationLog } from '../api/operations';
import { useWorld } from '../api/useWorld';
import { useForceCloseWorktree, useRemoveWorktree, useTrashWorktree } from '../api/worktrees';
import { useHrefFor, useLinkState } from '../nav/useNav';
import type { Operation, OperationStep } from '../protocol/entities';
import { operationLine } from '../selectors/operations';
import { clockTime, durationOf } from '../protocol/time';
import { actionIcon, stepIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import { ConfirmButton } from '../ui/ConfirmButton';
import { removeConfirm, shelveConfirm, trashConfirm } from '../worktrees/closeConfirms';
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
      {op.state === 'refused' && op.kind === 'close' && <NextChains op={op} />}
    </section>
  );
}

/** The worktree the operation acted on, as a link to its card. */
function TargetLink({ op, onTarget }: { op: Operation; onTarget: () => void }) {
  const to = useHrefFor({ card: { kind: 'worktree', id: op.worktreeId } });
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
      {op.worktreeId}
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

type Chain = 'shelve' | 'trash' | 'delete';

/**
 * What a refused close can do next: the closes that skip its checks, each with
 * the confirm the close control asks.
 */
function NextChains({ op }: { op: Operation }) {
  const worktree = useWorld().world.worktrees[op.worktreeId];
  const shelve = useForceCloseWorktree();
  const trash = useTrashWorktree();
  const remove = useRemoveWorktree();
  const [asking, setAsking] = useState<Chain | null>(null);
  if (!worktree || worktree.isClosed) return null;
  const target = { worktreeId: worktree.id };
  const chains: {
    chain: Chain;
    label: string;
    icon: Parameters<typeof actionIcon>[0];
    confirm: typeof shelveConfirm;
    run: () => Promise<unknown>;
  }[] = [
    {
      chain: 'shelve',
      label: 'Shelve',
      icon: 'archive',
      confirm: shelveConfirm,
      run: () => shelve.mutateAsync(target),
    },
    {
      chain: 'trash',
      label: 'Trash',
      icon: 'delete',
      confirm: trashConfirm,
      run: () => trash.mutateAsync(target),
    },
    {
      chain: 'delete',
      label: 'Delete',
      icon: 'removeWorktree',
      confirm: removeConfirm,
      run: () => remove.mutateAsync(target),
    },
  ];
  return (
    <div className={styles.next} role="group" aria-label="Next">
      {chains.map(({ chain, label, icon, confirm, run }) => {
        const { question, confirm: yes } = confirm(worktree);
        return (
          <ConfirmButton
            key={chain}
            icon={actionIcon(icon)}
            question={question}
            confirm={yes}
            asking={asking === chain}
            onAsk={() => setAsking(chain)}
            onDismiss={() => setAsking(null)}
            onConfirm={run}
          >
            {label}
          </ConfirmButton>
        );
      })}
    </div>
  );
}
