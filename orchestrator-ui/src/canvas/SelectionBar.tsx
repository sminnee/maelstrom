import { useId, useMemo, useState } from 'react';
import { useComms } from '../api/comms';
import { useDismiss, useTakeOffDesk } from '../api/desk';
import { useSetStatus, useUpdateTask } from '../api/tasks';
import { useWorld } from '../api/useWorld';
import { TASK_STATUSES, type TaskStatus } from '../protocol/entities';
import {
  linkedComms,
  linkTargets,
  planDismiss,
  statusTargets,
  unlinkTargets,
  type DismissStep,
} from '../selectors/bulkTargets';
import { isLive, type GraphNode } from '../selectors/graph';
import { OffDeskIcon } from '../shell/OffDeskIcon';
import { actionIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import { ComboBox } from '../ui/ComboBox';
import { ConfirmButton } from '../ui/ConfirmButton';
import { useRecent } from '../ui/recent';
import { useBulkRun } from '../ui/useBulkRun';
import styles from './SelectionBar.module.css';

/**
 * The canvas **Bulk bar**: one action applied to every node of the
 * **Selection** it fits. See `selectors/bulkTargets.ts` for which nodes each
 * action skips.
 */
export function SelectionBar({
  nodes,
  failure,
  onDone,
  onClear,
}: {
  nodes: GraphNode[];
  /** Why the last run failed. */
  failure: string | null;
  /** Called after a run with the node ids now done, and why the rest are not. */
  onDone: (done: string[], failure: string | null) => void;
  onClear: () => void;
}) {
  const { world } = useWorld();
  const setStatus = useSetStatus();
  const updateTask = useUpdateTask();
  const dismiss = useDismiss();
  const takeOffDesk = useTakeOffDesk();
  const comms = useComms().data?.comms;
  const recent = useRecent('comm');
  const bulk = useBulkRun();
  const { busy } = bulk;
  const [linking, setLinking] = useState('');
  const [unlinking, setUnlinking] = useState('');
  const [asking, setAsking] = useState(false);
  const linkId = useId();

  const linkOptions = useMemo(
    () => (comms ?? []).filter((c) => !c.closedAt).map((c) => ({ value: c.id, label: c.title })),
    [comms],
  );
  const titles = useMemo(() => new Map((comms ?? []).map((c) => [c.id, c.title])), [comms]);
  const linkTarget = linkOptions.some((o) => o.value === linking.trim()) ? linking.trim() : null;
  const linked = linkedComms(nodes);
  const plan = planDismiss(world, nodes);
  const ending = plan.filter((s) => isLive(s.node.agent)).length;

  // A node the run skips has nothing to do, so it is done too.
  const finish = (refused: string[], failure: string | null) =>
    onDone(
      nodes.map((n) => n.id).filter((id) => !refused.includes(id)),
      failure,
    );
  const run = async (targets: GraphNode[], send: (node: GraphNode) => Promise<unknown>) => {
    const { failed, failure } = await bulk.run(targets, send);
    finish(
      failed.map((n) => n.id),
      failure,
    );
  };
  const setComms = (node: GraphNode, next: string[]) =>
    updateTask.mutateAsync({ taskId: node.task!.id, fields: { comms: next } });
  const runDismiss = async () => {
    const { failed, failure } = await bulk.run(plan, (step: DismissStep) =>
      step.chain === 'offDesk'
        ? takeOffDesk.mutateAsync({ id: step.deskId })
        : dismiss.mutateAsync({
            deskId: step.deskId,
            chain: step.chain,
            worktreeId: step.worktreeId,
          }),
    );
    finish(
      failed.map((s) => s.node.id),
      failure,
    );
  };

  return (
    <section className={`${styles.bar} nowheel nopan nodrag`} aria-label="Bulk actions">
      <span>{nodes.length} selected</span>
      {/* Applies on change, so the value always rests on the placeholder. */}
      <select
        aria-label="Set status"
        value=""
        disabled={busy}
        onChange={(e) => {
          const status = e.target.value as TaskStatus;
          void run(statusTargets(nodes, status), (n) =>
            setStatus.mutateAsync({ taskId: n.task!.id, status }),
          );
        }}
      >
        <option value="" disabled>
          Set status…
        </option>
        {TASK_STATUSES.map((status) => (
          <option key={status} value={status}>
            {status}
          </option>
        ))}
      </select>
      <span className={styles.group}>
        <label htmlFor={linkId}>Comm</label>
        <ComboBox
          id={linkId}
          value={linking}
          options={linkOptions}
          recent={recent.ids}
          onChange={setLinking}
          placeholder="Link to…"
        />
        <AppButton
          icon={actionIcon('link')}
          disabled={busy || !linkTarget}
          onClick={async () => {
            if (!linkTarget) return;
            await run(linkTargets(nodes, linkTarget), (n) =>
              setComms(n, [...n.task!.comms, linkTarget]),
            );
            recent.touch(linkTarget);
            setLinking('');
          }}
        >
          Link
        </AppButton>
      </span>
      {linked.length > 0 && (
        <span className={styles.group}>
          <select
            aria-label="Unlink comm"
            value={unlinking}
            disabled={busy}
            onChange={(e) => setUnlinking(e.target.value)}
          >
            <option value="" disabled>
              Unlink from…
            </option>
            {linked.map((id) => (
              <option key={id} value={id}>
                {titles.get(id) ?? id}
              </option>
            ))}
          </select>
          <AppButton
            icon={actionIcon('unlink')}
            disabled={busy || !linked.includes(unlinking)}
            onClick={async () => {
              const commId = unlinking;
              await run(unlinkTargets(nodes, commId), (n) =>
                setComms(
                  n,
                  n.task!.comms.filter((c) => c !== commId),
                ),
              );
              setUnlinking('');
            }}
          >
            Unlink
          </AppButton>
        </span>
      )}
      {ending > 0 ? (
        <ConfirmButton
          icon={<OffDeskIcon />}
          disabled={busy}
          question={`Dismiss ${plan.length}? This terminates ${ending} running ${ending === 1 ? 'agent' : 'agents'}.`}
          confirm="Dismiss them"
          asking={asking}
          onAsk={() => setAsking(true)}
          onDismiss={() => setAsking(false)}
          onConfirm={runDismiss}
        >
          Dismiss
        </ConfirmButton>
      ) : (
        <AppButton icon={<OffDeskIcon />} disabled={busy || plan.length === 0} onClick={runDismiss}>
          Dismiss
        </AppButton>
      )}
      <AppButton disabled={busy} onClick={onClear}>
        Clear
      </AppButton>
      {failure && (
        <span className={styles.failure} role="alert">
          {failure}
        </span>
      )}
    </section>
  );
}
