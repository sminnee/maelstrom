import type { ForcedBeat } from '../../protocol/backend';
import type { Agent, Task } from '../../protocol/entities';
import type { ServerEvent } from '../../protocol/events';
import type { AgentId } from '../../protocol/ids';
import type { NormaliseContext, RawStreamEvent } from '../../protocol/normalise';
import { contextForAgent, markExited, normaliseStreamEvent } from '../../protocol/normalise';
import { isActionable } from '../../protocol/phase';
import type { ClientState } from '../../protocol/reducer';
import { applyEvent } from '../../protocol/reducer';
import type { Beat } from './scripts';
import { MULTI_QUESTIONS, expandBeat, permissionBeat, planMarkdown, scriptFor } from './scripts';
import type { Rng } from './rng';

export interface Cursor {
  beats: Beat[];
  beatIndex: number;
  /** Raw events already expanded from the current beat, one per tick. */
  pending: RawStreamEvent[];
}

export interface AgentSim {
  ctx: NormaliseContext;
  cursor: Cursor;
}

/** Everything the simulation remembers that is not in the world. */
export interface SimWorld {
  agents: Record<AgentId, AgentSim>;
  force: ForcedBeat[];
  counter: number;
}

export function initialSimState(state: ClientState): SimWorld {
  const agents: Record<AgentId, AgentSim> = {};
  for (const agent of Object.values(state.world.agents)) {
    agents[agent.id] = {
      ctx: contextForAgent(state, agent.id),
      cursor: { beats: [], beatIndex: 0, pending: [] },
    };
  }
  return { agents, force: [], counter: 0 };
}

export interface StepResult {
  events: ServerEvent[];
  sim: SimWorld;
}

/**
 * Advance the world one tick: every processing agent plays one raw event of
 * its script through the normaliser. Pure: same inputs, same events.
 */
export function step(state: ClientState, sim: SimWorld, rng: Rng, now: string): StepResult {
  const run = new Run(state, sim, rng, now);
  for (const agent of Object.values(state.world.agents)) run.stepAgent(agent);
  return run.result();
}

/** Launch an agent for `task`, as `mael task next --run` would. */
export function launchAgent(
  state: ClientState,
  sim: SimWorld,
  task: Task,
  now: string,
  model?: string,
): StepResult & { agentId: AgentId } {
  const run = new Run(state, sim, () => 0, now);
  const agentId = run.launch(task, model);
  return { ...run.result(), agentId };
}

/** Mark an agent's process gone, as the daemon does when the child exits. */
export function exitAgent(
  state: ClientState,
  sim: SimWorld,
  agentId: AgentId,
  exitCode: number,
  now: string,
): StepResult {
  const run = new Run(state, sim, () => 0, now);
  const agent = state.world.agents[agentId];
  if (agent) run.exit(agent, exitCode);
  return run.result();
}

/** The events an agent-directed command produces, and the script it queues. */
export function onCommand(
  state: ClientState,
  sim: SimWorld,
  agentId: AgentId,
  raw: RawStreamEvent[],
  follow: Beat[],
  now: string,
): StepResult {
  const run = new Run(state, sim, () => 0, now);
  for (const event of raw) run.feed(agentId, event);
  run.queue(agentId, follow);
  return run.result();
}

class Run {
  private events: ServerEvent[] = [];
  private sim: SimWorld;
  private state: ClientState;
  private rng: Rng;
  private now: string;

  constructor(state: ClientState, sim: SimWorld, rng: Rng, now: string) {
    this.state = state;
    this.rng = rng;
    this.now = now;
    this.sim = { ...sim, agents: { ...sim.agents }, force: [...sim.force] };
  }

  result(): StepResult {
    return { events: this.events, sim: this.sim };
  }

  private nextId(prefix: string): string {
    this.sim.counter += 1;
    return `${prefix}_${this.sim.counter.toString(36).padStart(6, '0')}`;
  }

  private simFor(agent: Agent): AgentSim {
    const existing = this.sim.agents[agent.id];
    if (existing) return existing;
    const created = {
      ctx: contextForAgent(this.state, agent.id),
      cursor: { beats: [], beatIndex: 0, pending: [] },
    };
    this.sim.agents[agent.id] = created;
    return created;
  }

  private emit(events: ServerEvent[]) {
    for (const event of events) {
      this.state = applyEvent(this.state, event);
      this.events.push(event);
    }
  }

  /** One raw event through the normaliser, into the world. */
  feed(agentId: AgentId, raw: RawStreamEvent) {
    const agent = this.state.world.agents[agentId];
    if (!agent) return;
    const entry = this.simFor(agent);
    const out = normaliseStreamEvent(this.state, entry.ctx, raw, this.now);
    this.sim.agents[agentId] = { ...entry, ctx: out.ctx };
    this.emit(out.events);
  }

  /** Put beats in front of whatever the agent was going to do next. */
  queue(agentId: AgentId, beats: Beat[]) {
    const agent = this.state.world.agents[agentId];
    if (!agent || beats.length === 0) return;
    const entry = this.simFor(agent);
    const cursor = entry.cursor;
    this.sim.agents[agentId] = {
      ...entry,
      cursor: {
        beats: [
          ...cursor.beats.slice(0, cursor.beatIndex),
          ...beats,
          ...cursor.beats.slice(cursor.beatIndex),
        ],
        beatIndex: cursor.beatIndex,
        pending: cursor.pending,
      },
    };
  }

  stepAgent(agent: Agent) {
    const forced = this.takeForce(agent.id);
    if (forced?.kind === 'exit') {
      this.exit(agent, forced.exitCode);
      return;
    }
    // The forced beat plays after the current beat's remaining events, so an
    // open tool call still gets its result.
    if (forced) this.queue(agent.id, [this.forcedBeat(forced, agent)]);

    const current = this.state.world.agents[agent.id]!;
    const entry = this.simFor(current);
    if (current.state !== 'processing' && !(current.state === 'idle' && this.hasWork(entry.cursor)))
      return;

    let cursor = entry.cursor;
    if (cursor.pending.length === 0) {
      if (cursor.beats.length === 0) {
        const task = this.state.world.tasks[current.taskId];
        cursor = { ...cursor, beats: task ? scriptFor(current.phase, task, this.rng) : [] };
      }
      const beat = cursor.beats[cursor.beatIndex];
      if (!beat) {
        // Script exhausted while still processing: end the turn.
        this.feed(current.id, {
          type: 'result',
          subtype: 'success',
          total_cost_usd: current.costUsd,
          duration_ms: 0,
        });
        return;
      }
      cursor = { ...cursor, beatIndex: cursor.beatIndex + 1 };
      if (beat.kind === 'exit') {
        this.sim.agents[current.id] = { ...entry, cursor };
        this.exit(current, beat.exitCode);
        return;
      }
      const raw = expandBeat(beat, { next: (p) => this.nextId(p) }, current.session);
      if (beat.kind === 'finish')
        raw.push({ type: 'mael_finish', document: beat.document ?? null });
      cursor = { ...cursor, pending: raw };
    }
    const [next, ...rest] = cursor.pending;
    this.sim.agents[current.id] = { ...this.simFor(current), cursor: { ...cursor, pending: rest } };
    if (!next) return;
    if (next.type === 'mael_finish') {
      this.finish(
        current,
        next.document as {
          kind: 'pr' | 'tasks' | 'review';
          title: string;
          markdown: string;
        } | null,
      );
    } else {
      this.feed(current.id, next);
    }
  }

  private hasWork(cursor: Cursor): boolean {
    return (
      cursor.pending.length > 0 ||
      (cursor.beats.length > 0 && cursor.beatIndex < cursor.beats.length)
    );
  }

  private takeForce(agentId: AgentId): ForcedBeat | undefined {
    const index = this.sim.force.findIndex((f) => f.agentId === agentId);
    if (index === -1) return undefined;
    const [forced] = this.sim.force.splice(index, 1);
    return forced;
  }

  private forcedBeat(forced: ForcedBeat, agent: Agent): Beat {
    const task = this.state.world.tasks[agent.taskId];
    switch (forced.kind) {
      case 'ask':
        // The forced question is the two-question set, so the drawer exercises stepping.
        return { kind: 'ask', questions: MULTI_QUESTIONS };
      case 'permission':
        return permissionBeat(this.rng);
      case 'plan':
        return { kind: 'plan', markdown: task ? planMarkdown(task) : '# Plan\n' };
      case 'finish':
        return { kind: 'finish' };
      case 'exit':
        return { kind: 'exit', exitCode: forced.exitCode };
    }
  }

  exit(agent: Agent, exitCode: number) {
    const entry = this.simFor(agent);
    const out = markExited(this.state, entry.ctx, exitCode, this.now);
    this.sim.agents[agent.id] = {
      ...entry,
      ctx: out.ctx,
      cursor: { beats: [], beatIndex: 0, pending: [] },
    };
    this.emit(out.events);
  }

  /**
   * The notebook side of a turn that finished the task: the task is done, its
   * followers may now be actionable, and each newly actionable one launches.
   */
  private finish(
    agent: Agent,
    document: { kind: 'pr' | 'tasks' | 'review'; title: string; markdown: string } | null,
  ) {
    const task = this.state.world.tasks[agent.taskId];
    if (!task) return;
    if (document) {
      const id = this.nextId('doc');
      this.emit([
        {
          type: 'upsert',
          kind: 'document',
          entity: {
            id,
            agentId: agent.id,
            taskId: task.id,
            kind: document.kind,
            title: document.title,
            markdown: document.markdown,
            version: 1,
            status: 'awaiting-review',
            source:
              document.kind === 'pr'
                ? { type: 'pr', number: 100 + this.sim.counter }
                : { type: 'draft_files', paths: [document.title] },
          },
        },
        {
          type: 'upsert',
          kind: 'attention',
          entity: {
            id: `att-${id}`,
            kind: 'document_review',
            agentId: agent.id,
            taskId: task.id,
            documentId: id,
            requestId: null,
            summary: `${document.title} awaiting review`,
            raisedAt: this.now,
            clearedAt: null,
          },
        },
      ]);
    }
    this.emit([
      {
        type: 'upsert',
        kind: 'task',
        entity: { ...task, status: 'done', actionable: false, updated: this.now },
      },
    ]);
    for (const follower of Object.values(this.state.world.tasks)) {
      if (!follower.follows.includes(task.id)) continue;
      const unblocked: Task = {
        ...follower,
        status: follower.status === 'blocked' ? 'todo' : follower.status,
      };
      const actionable = isActionable(unblocked, this.state.world.tasks);
      const changed = actionable !== follower.actionable || unblocked.status !== follower.status;
      if (changed) {
        this.emit([
          { type: 'upsert', kind: 'task', entity: { ...unblocked, actionable, updated: this.now } },
        ]);
      }
      if (actionable && unblocked.status === 'todo') this.launch(unblocked);
    }
  }

  /** An agent for `task` in the open worktree holding its branch, else a closed one to recycle. */
  launch(task: Task, model?: string): AgentId {
    const worktrees = Object.values(this.state.world.worktrees);
    const worktree =
      worktrees.find(
        (w) => w.project === task.project && w.branch === task.branch && !w.isClosed,
      ) ?? worktrees.find((w) => w.project === task.project && w.isClosed);
    this.sim.counter += 1;
    const id = `ag${this.sim.counter.toString(16).padStart(6, '0')}`;
    const agent: Agent = {
      id,
      state: 'processing',
      session: `sess-${id}`,
      cwd: worktree?.path ?? '',
      model: model ?? 'claude-opus-5',
      waitingOn: '',
      lastMessage: '',
      costUsd: 0,
      taskId: task.id,
      project: task.project,
      worktreeId: worktree?.id ?? '',
      phase: task.phase,
      exitCode: null,
      pendingRequestId: null,
    };
    this.emit([
      {
        type: 'upsert',
        kind: 'task',
        entity: { ...task, status: 'in-progress', actionable: false, updated: this.now },
      },
      { type: 'upsert', kind: 'agent', entity: agent },
    ]);
    if (worktree?.isClosed) {
      this.emit([
        {
          type: 'upsert',
          kind: 'worktree',
          entity: { ...worktree, isClosed: false, branch: task.branch, sessionCount: 1 },
        },
      ]);
    }
    this.feed(id, {
      type: 'system',
      subtype: 'init',
      session_id: agent.session,
      model: agent.model,
    });
    return id;
  }
}
