import { useCallback, useEffect, useRef, useState } from 'react';
import { useMarkSeen, useOperations } from '../api/operations';
import { useLayoutMode } from '../layout/useLayoutMode';
import { useGo } from '../nav/useNav';
import type { Operation } from '../protocol/entities';
import type { OperationId } from '../protocol/ids';
import { isUnseen, unitOperation, type Unit } from '../selectors/operations';
import { useAppStore } from '../store/store';

/** How long a done operation stays in the strip, so the operator sees it end. */
const DONE_LINGER_MS = 4000;

const NONE: Operation[] = [];

/** Every operation the server holds, newest first. Empty while the list loads. */
export function useOperationList(): Operation[] {
  return useOperations().data?.operations ?? NONE;
}

/**
 * The operation that marks a unit on the board, if one does. Every board
 * surface reads through it, so they cannot disagree.
 */
export function useUnitOperation(unit: Unit): Operation | undefined {
  return unitOperation(useOperationList(), unit);
}

/** Showing an **Unseen** operation marks it seen, once; a failed mark tries again. */
export function useMarkSeenOnShow(shown: Operation | null | undefined) {
  const markSeen = useMarkSeen();
  const posted = useRef(new Set<OperationId>());
  useEffect(() => {
    if (!shown || !isUnseen(shown) || posted.current.has(shown.id)) return;
    const id = shown.id;
    posted.current.add(id);
    markSeen.mutate({ operationId: id }, { onError: () => posted.current.delete(id) });
  }, [shown, markSeen]);
}

/**
 * The operations the strip shows: what is **Unseen**, what runs, and what
 * ended done a moment ago, in that order. See docs/dev/orchestrator-ui.md,
 * "The console".
 */
export function useStripOperations(ops: readonly Operation[]): Operation[] {
  // Each operation this page saw run, then end done. Read off the change from
  // the last list to this one, during the render.
  const [last, setLast] = useState(ops);
  const [leaving, setLeaving] = useState<ReadonlySet<OperationId>>(new Set());
  if (ops !== last) {
    setLast(ops);
    const wasRunning = new Set(last.filter((op) => op.state === 'running').map((op) => op.id));
    const ended = ops.filter((op) => op.state === 'done' && wasRunning.has(op.id));
    if (ended.length > 0) setLeaving((now) => new Set([...now, ...ended.map((op) => op.id)]));
  }

  // Each one leaves on its own timer, started as it arrives.
  const timers = useRef(new Map<OperationId, ReturnType<typeof setTimeout>>());
  useEffect(() => {
    for (const id of leaving) {
      if (timers.current.has(id)) continue;
      const timer = setTimeout(() => {
        timers.current.delete(id);
        setLeaving((now) => new Set([...now].filter((other) => other !== id)));
      }, DONE_LINGER_MS);
      timers.current.set(id, timer);
    }
  }, [leaving]);
  useEffect(() => {
    const running = timers.current;
    return () => running.forEach(clearTimeout);
  }, []);

  // What waits on the operator leads; the work still moving follows it.
  return [
    ...ops.filter(isUnseen),
    ...ops.filter((op) => op.state === 'running'),
    ...ops.filter((op) => op.state === 'done' && leaving.has(op.id)),
  ];
}

/**
 * Go to what an operation acted on: the worktree's card on the desk. A phone
 * has no screen for a worktree's card, so it goes to the worktrees view. The
 * console closes, so the target is in view.
 */
export function useGoToTarget() {
  const narrow = useLayoutMode() === 'narrow';
  const go = useGo();
  const showPane = useAppStore((s) => s.showPane);
  const closeConsole = useAppStore((s) => s.closeConsole);
  return useCallback(
    (op: Operation) => {
      closeConsole();
      if (narrow) {
        go({ view: 'worktrees', card: null, panel: null });
        return;
      }
      showPane('canvas');
      go({ card: { kind: 'worktree', id: op.worktreeId } });
    },
    [narrow, go, showPane, closeConsole],
  );
}

/** Whether a key went to a field the user types in, where a key is text and not a command. */
export function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el?.closest) return false;
  return !!el.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"]');
}

/** Ctrl-` opens or closes the console from anywhere but a text field. Mounted once per shell. */
export function useConsoleKey() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.code !== 'Backquote' && e.key !== '`') return;
      if (isTyping(e.target)) return;
      e.preventDefault();
      const { ui, openConsole, closeConsole } = useAppStore.getState();
      if (ui.console.open) closeConsole();
      else openConsole();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);
}
