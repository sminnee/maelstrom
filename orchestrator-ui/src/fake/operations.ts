import type { Operation, OperationKind, OperationStep } from '../protocol/entities';
import type { AgentId, OperationId, TaskId, WorktreeId } from '../protocol/ids';
import type { FakeServer } from './fakeServer';

/** The ids an operation learns while it runs, as the real `hook.target` sets them. */
export type FakeTarget = Partial<Pick<Operation, 'worktreeId' | 'taskId' | 'agentId'>>;

/**
 * One step of a fake operation. `refuse` is read when the step runs, so a
 * refusal judges the world as it is then, as the real step does. `run` is what
 * the step does to the world once it is done; `target` names an id it learned.
 */
export interface FakeStep {
  name: string;
  /** Read when the step ends, as the real step reports what it found. */
  lines?: string[] | (() => string[]);
  refuse?: () => string | undefined;
  run?: (target: (ids: FakeTarget) => void) => void;
}

/** A planted fault: words alone fail the last step; `step` names the one to fail. */
export type FakeFault = string | { step: string; words: string };

/** What one fake operation runs, and the words it is read by. */
export interface FakeOperationSpec {
  kind: OperationKind;
  /** What it holds alone while it runs: `worktree:<id>`, `task:<id>`, `agent:<id>`. */
  keys: string[];
  project?: string;
  worktreeId?: WorktreeId | null;
  taskId?: TaskId | null;
  agentId?: AgentId | null;
  /** While it runs: `Closing alpha`. */
  doing: string;
  /** Once done: `Closed alpha`. */
  done: string;
  /** The start of a fault: `Could not sync the worktree`. */
  failing: string;
  steps: FakeStep[];
}

/** The specs each server has run, so a retry runs the same steps again. */
const specs = new WeakMap<FakeServer, Map<OperationId, FakeOperationSpec>>();

const now = () => new Date().toISOString();

/**
 * The key a fault is planted under in `world.operationFaults`: the kind, and
 * the worktree, task or agent the operation acts on.
 */
export const faultKey = (kind: OperationKind, unit: string) => `${kind}:${unit}`;

/** The keys each running operation holds. A seeded one holds its worktree. */
function heldKeys(server: FakeServer): Map<string, OperationId> {
  const held = new Map<string, OperationId>();
  const known = specs.get(server);
  for (const op of Object.values(server.world.operations)) {
    if (op.state !== 'running') continue;
    const keys = known?.get(op.id)?.keys ?? (op.worktreeId ? [`worktree:${op.worktreeId}`] : []);
    for (const key of keys) held.set(key, op.id);
  }
  return held;
}

const busyOn = (server: FakeServer, keys: string[]) => {
  const held = heldKeys(server);
  return keys.map((k) => held.get(k)).find((id) => id !== undefined);
};

/**
 * Start `spec` on the timer, one step per `server.stepMs`, and answer with the
 * operation's id, as the real route answers before the work ends. A second
 * operation on a busy worktree is refused with the running one's id.
 */
export function startOperation(
  server: FakeServer,
  spec: FakeOperationSpec,
): { operationId: OperationId } | { busy: OperationId } {
  const { world } = server;
  const running = busyOn(server, spec.keys);
  if (running) return { busy: running };
  const numbers = Object.keys(world.operations).map((id) => Number(id.slice(2)));
  const id = `op${Math.max(0, ...numbers) + 1}`;
  const op: Operation = {
    id,
    kind: spec.kind,
    project: spec.project ?? '',
    worktreeId: spec.worktreeId ?? null,
    taskId: spec.taskId ?? null,
    agentId: spec.agentId ?? null,
    words: spec.doing,
    state: 'running',
    startedAt: now(),
    endedAt: null,
    seen: false,
    steps: spec.steps.map((s) => pending(s.name)),
  };
  world.operationLogs[id] = {};
  const known = specs.get(server) ?? new Map<OperationId, FakeOperationSpec>();
  known.set(id, spec);
  specs.set(server, known);
  put(server, op);
  runFrom(server, id, spec);
  return { operationId: id };
}

/**
 * Run a refused or failed operation again, from its first step that did not
 * finish. `null` when the fake never ran it, as after a reload of the page.
 */
export function retryOperation(
  server: FakeServer,
  id: OperationId,
): { operationId: OperationId } | { busy: OperationId } | null {
  const spec = specs.get(server)?.get(id);
  const op = server.world.operations[id];
  if (!spec || !op) return null;
  const running = busyOn(server, spec.keys);
  if (running) return { busy: running };
  put(server, {
    ...op,
    state: 'running',
    words: spec.doing,
    endedAt: null,
    seen: false,
    steps: op.steps.map((s) => (s.state === 'done' ? s : pending(s.name))),
  });
  runFrom(server, id, spec);
  return { operationId: id };
}

function pending(name: string): OperationStep {
  return { name, words: '', state: 'pending', startedAt: null, endedAt: null };
}

function put(server: FakeServer, op: Operation) {
  server.change({ kind: 'operation', ids: [op.id] }, (w) => {
    w.operations[op.id] = op;
  });
}

function moveStep(
  server: FakeServer,
  id: OperationId,
  name: string,
  patch: Partial<OperationStep>,
) {
  const op = server.world.operations[id]!;
  put(server, {
    ...op,
    steps: op.steps.map((s) => (s.name === name ? { ...s, ...patch } : s)),
  });
}

function end(server: FakeServer, id: OperationId, state: Operation['state'], words: string) {
  const op = server.world.operations[id]!;
  put(server, { ...op, state, words, endedAt: now(), seen: state === 'done' });
}

/** Run each step of `spec` the operation has not done, one timer tick each. */
function runFrom(server: FakeServer, id: OperationId, spec: FakeOperationSpec) {
  const left = spec.steps.filter(
    (s) => server.world.operations[id]!.steps.find((o) => o.name === s.name)?.state !== 'done',
  );
  const fault = [spec.worktreeId, spec.taskId, spec.agentId]
    .filter((unit): unit is string => !!unit)
    .map((unit) => server.world.operationFaults[faultKey(spec.kind, unit)])
    .find((f) => f !== undefined);
  const faultStep =
    fault === undefined ? undefined : typeof fault === 'string' ? left.at(-1)?.name : fault.step;
  const faultWords = typeof fault === 'string' ? fault : fault?.words;
  const target = (ids: FakeTarget) => {
    const op = server.world.operations[id]!;
    put(server, { ...op, ...ids });
  };
  const next = (i: number) => {
    const step = left[i];
    if (!step) {
      end(server, id, 'done', spec.done);
      return;
    }
    moveStep(server, id, step.name, { state: 'running', startedAt: now() });
    setTimeout(() => {
      const lines = typeof step.lines === 'function' ? step.lines() : (step.lines ?? []);
      server.world.operationLogs[id]![step.name] = lines;
      // A planted fault fails its step: the last, as a git error at the end
      // would, unless it names another.
      if (faultWords !== undefined && step.name === faultStep) {
        moveStep(server, id, step.name, { state: 'failed', words: faultWords, endedAt: now() });
        end(server, id, 'failed', `${spec.failing}: ${faultWords}`);
        return;
      }
      const refused = step.refuse?.();
      if (refused !== undefined) {
        moveStep(server, id, step.name, { state: 'refused', words: refused, endedAt: now() });
        end(server, id, 'refused', refused);
        return;
      }
      step.run?.(target);
      moveStep(server, id, step.name, { state: 'done', endedAt: now() });
      next(i + 1);
    }, server.stepMs);
  };
  next(0);
}
