import { useCallback, useEffect, useRef, useState } from 'react';
import { useRetryOperation } from '../api/operations';
import type { Operation } from '../protocol/entities';
import type { OperationId } from '../protocol/ids';
import {
  consoleOrder,
  filterOperations,
  isPinned,
  isUnseen,
  operationLine,
  operationTarget,
  type ConsoleFilter,
} from '../selectors/operations';
import { useAppStore } from '../store/store';
import { actionIcon } from '../ui/actionIcons';
import { SegmentedControl } from '../ui/SegmentedControl';
import { OperationLog } from './OperationLog';
import { useTick } from './useTick';
import { clockTime } from '../protocol/time';
import { StepMeter } from '../ui/StepMeter';
import { isTyping, useGoToTarget, useMarkSeenOnShow, useOperationList } from './useConsole';
import styles from './Console.module.css';

const FILTERS: { filter: ConsoleFilter; label: string }[] = [
  { filter: 'running', label: 'Running' },
  { filter: 'problems', label: 'Problems' },
  { filter: 'all', label: 'All' },
];

/** The id the console carries: focus inside it returns on close. */
const CONSOLE_ID = 'console';

/** The least height the drawer is dragged to: the filter row and two rows of the list. */
const MIN_HEIGHT = 160;

/**
 * The **Console**: every operation, and the log of the one selected. A drawer
 * over the foot of the body on a wide screen; a pushed screen on a phone.
 *
 * `open` false is a drawer on its way out: it takes no keys or clicks, and
 * marks nothing seen. The shell keeps it in the page while its exit plays.
 */
export function Console({
  layout,
  open = true,
  ref,
}: {
  layout: 'drawer' | 'screen';
  open?: boolean;
  ref?: React.Ref<HTMLElement>;
}) {
  const ops = useOperationList();
  const selectedId = useAppStore((s) => s.ui.console.selected);
  const height = useAppStore((s) => s.ui.console.height);
  const select = useAppStore((s) => s.selectOperation);
  const closeConsole = useAppStore((s) => s.closeConsole);
  const goToTarget = useGoToTarget();
  const retry = useRetryOperation();
  const [filter, setFilter] = useState<ConsoleFilter>('all');
  const [text, setText] = useState('');
  const listRef = useRef<HTMLUListElement>(null);

  // What the list pinned stays pinned while it is open, so a row the operator
  // selects, and so sees, does not jump down under the pointer.
  const [held, setHeld] = useState<ReadonlySet<OperationId>>(new Set());
  const fresh = ops.filter((op) => isPinned(op) && !held.has(op.id));
  if (fresh.length > 0) setHeld(new Set([...held, ...fresh.map((op) => op.id)]));
  const listed = filterOperations(
    consoleOrder(ops, (op) => held.has(op.id) || isPinned(op)),
    filter,
    text,
  );
  const selected = listed.find((op) => op.id === selectedId) ?? listed[0] ?? null;
  useMarkSeenOnShow(open ? selected : null);

  // Focus moves in as the console opens, and back out as it closes. A strip
  // entry leaves the page as the console opens, so it has nothing to return to.
  // A drawer gives focus back as its exit starts. A layout effect would not:
  // React refocuses the list if focus moves during the commit.
  useEffect(() => {
    if (!open) return;
    const before = document.activeElement as HTMLElement | null;
    listRef.current?.focus();
    return () => {
      // Only when focus is still in the console, or nowhere: a go to the target moves it on.
      const active = document.activeElement;
      const inConsole = !active || active === document.body || active.closest(`#${CONSOLE_ID}`);
      if (!inConsole) return;
      if (before && before !== document.body && before.isConnected) before.focus();
    };
  }, [open]);

  // The selected row stays in view as the arrows move it.
  useEffect(() => {
    if (!selected) return;
    document.getElementById(optionId(selected.id))?.scrollIntoView?.({ block: 'nearest' });
  }, [selected]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      closeConsole();
      return;
    }
    if (isTyping(e.target) || e.ctrlKey || e.metaKey || e.altKey) return;
    const at = selected ? listed.indexOf(selected) : -1;
    // The arrows move the list only from the list: from a button in the log they
    // would remount the log under the focus and drop it to the body.
    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && e.target === listRef.current) {
      e.preventDefault();
      const next =
        listed[Math.max(0, Math.min(listed.length - 1, at + (e.key === 'ArrowDown' ? 1 : -1)))];
      if (next) select(next.id);
    } else if (e.key === 'Enter' && e.target === listRef.current && selected) {
      e.preventDefault();
      goToTarget(selected);
    } else if ((e.key === 'r' || e.key === 'R') && selected && canRetry(selected)) {
      e.preventDefault();
      retry.mutate({ operationId: selected.id });
    }
  };

  return (
    <section
      ref={ref}
      id={CONSOLE_ID}
      className={styles.console}
      data-layout={layout}
      aria-label="Console"
      style={layout === 'drawer' && height !== null ? { height } : undefined}
      onKeyDown={onKeyDown}
      data-presence={open ? 'in' : 'out'}
      inert={!open || undefined}
    >
      {layout === 'drawer' && (
        <>
          <Grip />
          <button
            type="button"
            className={`bareButton ${styles.close}`}
            aria-label="Close console"
            onClick={closeConsole}
          >
            {actionIcon('close')}
          </button>
        </>
      )}
      <div className={styles.side}>
        <div className={styles.filters}>
          <SegmentedControl
            label="Show"
            items={FILTERS.map(({ filter: f, label }) => ({ id: f, label }))}
            pressed={filter}
            onPress={setFilter}
          />
          <input
            type="search"
            className={styles.search}
            aria-label="Filter operations"
            placeholder="Filter"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </div>
        <OperationList
          ref={listRef}
          ops={listed}
          empty={ops.length === 0 ? 'No operations yet.' : 'No operation matches.'}
          selected={selected}
          onSelect={(op) => select(op.id)}
        />
      </div>
      <div className={styles.main}>
        {selected ? (
          <OperationLog
            key={selected.id}
            op={selected}
            onTarget={() => goToTarget(selected)}
            onRetry={
              canRetry(selected)
                ? // The log head reports a failure, for the button and for R alike.
                  () => retry.mutateAsync({ operationId: selected.id }).catch(() => {})
                : undefined
            }
            retryError={retry.variables?.operationId === selected.id ? retry.error : null}
          />
        ) : null}
      </div>
    </section>
  );
}

const canRetry = (op: Operation) => op.state === 'refused' || op.state === 'failed';

const optionId = (id: OperationId) => `${CONSOLE_ID}-op-${id}`;

/** The list, with the selected row marked. One tab stop; the arrows move in it. */
function OperationList({
  ref,
  ops,
  empty,
  selected,
  onSelect,
}: {
  ref: React.Ref<HTMLUListElement>;
  ops: Operation[];
  empty: string;
  selected: Operation | null;
  onSelect: (op: Operation) => void;
}) {
  const now = useTick(ops.some((op) => op.state === 'running'));
  return (
    <ul
      ref={ref}
      className={styles.list}
      role="listbox"
      aria-label="Operations"
      tabIndex={0}
      aria-activedescendant={selected ? optionId(selected.id) : undefined}
    >
      {ops.length === 0 && <li className={styles.empty}>{empty}</li>}
      {ops.map((op) => (
        <li
          key={op.id}
          id={optionId(op.id)}
          role="option"
          className={styles.option}
          aria-selected={op === selected}
          data-operation-id={op.id}
          data-state={op.state}
          data-unseen={isUnseen(op) || undefined}
          onClick={() => onSelect(op)}
        >
          <StepMeter op={op} />
          <span className={`${styles.line} truncate`}>{operationLine(op)}</span>
          <span className={`${styles.target} truncate`}>{operationTarget(op).label}</span>
          <time className={`${styles.when} nowrap`} dateTime={op.startedAt}>
            {clockTime(op.startedAt, now)}
          </time>
        </li>
      ))}
    </ul>
  );
}

/** The drawer's top edge. A drag on it sets the drawer's height. */
function Grip() {
  const setHeight = useAppStore((s) => s.setConsoleHeight);
  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const grip = e.currentTarget;
      const drawer = grip.parentElement!;
      const body = drawer.parentElement!;
      const startY = e.clientY;
      const startHeight = drawer.getBoundingClientRect().height;
      grip.setPointerCapture(e.pointerId);
      const onMove = (ev: PointerEvent) => {
        const max = body.getBoundingClientRect().height - MIN_HEIGHT / 4;
        setHeight(Math.max(MIN_HEIGHT, Math.min(max, startHeight - (ev.clientY - startY))));
      };
      const onEnd = () => {
        grip.removeEventListener('pointermove', onMove);
        grip.removeEventListener('pointerup', onEnd);
        grip.removeEventListener('pointercancel', onEnd);
      };
      grip.addEventListener('pointermove', onMove);
      grip.addEventListener('pointerup', onEnd);
      grip.addEventListener('pointercancel', onEnd);
    },
    [setHeight],
  );
  return <div className={styles.grip} onPointerDown={onPointerDown} aria-hidden="true" />;
}
