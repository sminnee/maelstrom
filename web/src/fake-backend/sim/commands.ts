import type { Command, CommandError, ResultMap } from '../../protocol/commands';
import type { Document } from '../../protocol/documents';
import type { Task } from '../../protocol/entities';
import type { ServerEvent } from '../../protocol/events';
import type { RawStreamEvent } from '../../protocol/normalise';
import { PLAN_TOOL, QUESTION_TOOL } from '../../protocol/normalise';
import { isActionable } from '../../protocol/phase';
import { validateCommand } from '../../protocol/validate';
import type { ClientState } from '../../protocol/reducer';
import { applyEvent } from '../../protocol/reducer';
import type { Beat } from './scripts';
import { TASK_CHAIN, replyScript, reviseScript } from './scripts';
import type { SimWorld } from './stepper';
import { exitAgent, launchAgent, onCommand } from './stepper';

/** A delegated command the world cannot take; the backend answers with its error. */
export class CommandRefused extends Error {
  error: CommandError;
  constructor(error: CommandError) {
    super(error.message);
    this.error = error;
  }
}

export interface Consequence {
  events: ServerEvent[];
  sim: SimWorld;
  result: ResultMap[Command['type']];
}

/**
 * What the world does because of a validated command. Agent-directed commands
 * become the daemon's own reply shapes (`control_response`, a `user` turn)
 * and go through the normaliser, so the fake takes the path a real backend
 * would. Notebook-side commands are events straight away.
 */
export function applyCommand(
  state: ClientState,
  sim: SimWorld,
  cmd: Command,
  now: string,
): Consequence {
  const { world } = state;
  switch (cmd.type) {
    case 'agent.approve':
    case 'agent.deny':
    case 'agent.answer': {
      const agent = world.agents[cmd.agentId]!;
      const pending = sim.agents[agent.id]?.ctx.pending;
      const input = pending?.input ?? {};
      const response =
        cmd.type === 'agent.deny'
          ? { behavior: 'deny', message: cmd.reason }
          : cmd.type === 'agent.answer'
            ? { behavior: 'allow', updatedInput: { ...input, answers: cmd.answers } }
            : { behavior: 'allow', updatedInput: cmd.updatedInput ?? input };
      const raw: RawStreamEvent[] = [
        {
          type: 'control_response',
          response: { subtype: 'success', request_id: cmd.requestId, response },
        },
      ];
      if (pending?.toolUseId) {
        raw.push(
          toolResult(
            pending.toolUseId,
            resultTextFor(cmd, pending.tool),
            cmd.type === 'agent.deny',
          ),
        );
      }
      let follow: Beat[] = [];
      if (cmd.type === 'agent.deny' && pending?.tool === PLAN_TOOL) {
        const task = world.tasks[agent.taskId];
        if (task) follow = reviseScript(task);
      }
      const out = onCommand(state, sim, agent.id, raw, follow, now);
      return { ...out, result: {} };
    }
    case 'agent.say': {
      const raw: RawStreamEvent[] = [
        { type: 'user', message: { role: 'user', content: [{ type: 'text', text: cmd.text }] } },
      ];
      const out = onCommand(state, sim, cmd.agentId, raw, replyScript(cmd.text), now);
      return { ...out, result: {} };
    }
    case 'agent.stop': {
      const out = exitAgent(state, sim, cmd.agentId, 0, now);
      return { ...out, result: {} };
    }
    case 'agent.launch': {
      const task = world.tasks[cmd.taskId]!;
      const launched = launchAgent(state, sim, task, now, cmd.model);
      return { events: launched.events, sim: launched.sim, result: { agentId: launched.agentId } };
    }
    case 'document.approve':
    case 'document.requestChanges': {
      const doc = world.documents[cmd.documentId]!;
      const approved = cmd.type === 'document.approve';
      if (doc.source.type === 'plan_review') {
        // A plan is answered through the daemon: the same path as approve/deny.
        const unresolved = unresolvedComments(state, doc);
        const reason = [cmd.type === 'document.requestChanges' ? cmd.summary : '', ...unresolved]
          .filter(Boolean)
          .join('\n\n');
        const command: Command = approved
          ? { type: 'agent.approve', agentId: doc.agentId, requestId: doc.source.requestId }
          : {
              type: 'agent.deny',
              agentId: doc.agentId,
              requestId: doc.source.requestId,
              reason: reason || 'Changes requested',
            };
        // The document command validated the document; the agent must still
        // hold the request it maps to, or the answer has nowhere to go.
        const error = validateCommand(world, command);
        if (error) throw new CommandRefused(error);
        return applyCommand(state, sim, command, now);
      }
      const events: ServerEvent[] = [
        {
          type: 'upsert',
          kind: 'document',
          entity: { ...doc, status: approved ? 'approved' : 'changes-requested' },
        },
      ];
      for (const att of Object.values(world.attention)) {
        if (att.clearedAt === null && att.documentId === doc.id) {
          events.push({ type: 'upsert', kind: 'attention', entity: { ...att, clearedAt: now } });
        }
      }
      let next = state;
      for (const e of events) next = applyEvent(next, e);
      if (approved && doc.kind === 'tasks') {
        const created = promoteTasks(next, doc, now);
        events.push(...created);
      }
      const agent = world.agents[doc.agentId];
      if (!approved && agent && agent.state !== 'exited') {
        const body = [
          cmd.type === 'document.requestChanges' ? cmd.summary : '',
          ...unresolvedComments(state, doc),
        ]
          .filter(Boolean)
          .join('\n\n');
        const say = applyCommand(
          next,
          sim,
          {
            type: 'agent.say',
            agentId: agent.id,
            text: `Changes requested on ${doc.title} v${doc.version}:\n\n${body}`,
          },
          now,
        );
        return { events: [...events, ...say.events], sim: say.sim, result: {} };
      }
      return { events, sim, result: {} };
    }
    case 'comment.add': {
      const id = `cmt_${(sim.counter + 1).toString(36)}`;
      return {
        events: [
          {
            type: 'upsert',
            kind: 'comment',
            entity: {
              id,
              documentId: cmd.documentId,
              version: cmd.version,
              author: 'user',
              anchor: cmd.anchor,
              body: cmd.body,
              resolved: false,
              createdAt: now,
            },
          },
        ],
        sim: { ...sim, counter: sim.counter + 1 },
        result: { commentId: id },
      };
    }
    case 'comment.resolve': {
      const comment = world.comments[cmd.commentId]!;
      return {
        events: [{ type: 'upsert', kind: 'comment', entity: { ...comment, resolved: true } }],
        sim,
        result: {},
      };
    }
    case 'task.create': {
      const { task, sim: next } = newTask(sim, cmd.project, cmd.draft, '', now);
      return {
        events: [{ type: 'upsert', kind: 'task', entity: task }],
        sim: next,
        result: { taskId: task.id },
      };
    }
    case 'shaping.start': {
      const title = cmd.brief.split('\n')[0]?.slice(0, 60) || 'Shaping';
      const { task, sim: next } = newTask(
        sim,
        cmd.project,
        `# ${title}\n\n${cmd.brief}`,
        'shape',
        now,
      );
      const withTask = applyEvent(state, { type: 'upsert', kind: 'task', entity: task });
      const launched = launchAgent(withTask, next, task, now);
      return {
        events: [{ type: 'upsert', kind: 'task', entity: task }, ...launched.events],
        sim: launched.sim,
        result: { agentId: launched.agentId, taskId: task.id },
      };
    }
  }
}

function toolResult(toolUseId: string, content: string, isError: boolean): RawStreamEvent {
  return {
    type: 'user',
    message: {
      role: 'user',
      content: [{ type: 'tool_result', tool_use_id: toolUseId, content, is_error: isError }],
    },
  };
}

function resultTextFor(cmd: Command, tool: string): string {
  if (cmd.type === 'agent.deny') return cmd.reason;
  if (cmd.type === 'agent.answer') {
    return Object.entries(cmd.answers)
      .map(([q, a]) => `${q} → ${a}`)
      .join('\n');
  }
  if (tool === PLAN_TOOL) return 'User has approved exiting plan mode. You can now proceed.';
  if (tool === QUESTION_TOOL) return 'User did not answer the questions.';
  return 'Approved.';
}

function unresolvedComments(state: ClientState, doc: Document): string[] {
  return Object.values(state.world.comments)
    .filter((c) => c.documentId === doc.id && c.version === doc.version && !c.resolved)
    .map((c) => `> ${c.anchor.quote}\n\n${c.body}`);
}

function newTask(
  sim: SimWorld,
  project: string,
  content: string,
  command: string,
  now: string,
): { task: Task; sim: SimWorld } {
  const counter = sim.counter + 1;
  const id = `${project.slice(0, 4).toUpperCase()}-${100 + counter}`;
  const title = content.split('\n')[0]?.replace(/^#\s*/, '') || 'Untitled';
  const task: Task = {
    id,
    project,
    title,
    status: 'todo',
    command,
    mode: command ? 'normal' : 'auto',
    branch: `feat/${id.toLowerCase()}`,
    parent: '',
    follows: [],
    priority: 'normal',
    model: '',
    base: '',
    content,
    steps: [],
    log: [],
    created: now,
    updated: now,
    phase: command === 'shape' ? 'shaping' : 'executing',
    actionable: true,
  };
  return { task, sim: { ...sim, counter } };
}

/** Approving a task-set document promotes its tasks into the notebook. */
function promoteTasks(state: ClientState, doc: Document, now: string): ServerEvent[] {
  const parent = state.world.tasks[doc.taskId];
  if (!parent) return [];
  const events: ServerEvent[] = [];
  let previous = parent.id;
  let tasks = state.world.tasks;
  for (const spec of TASK_CHAIN) {
    const id = `${parent.id}.${spec.suffix}`;
    if (tasks[id]) continue;
    const task: Task = {
      ...parent,
      id,
      title: spec.title,
      status: 'todo',
      command: spec.command,
      mode: spec.command ? 'normal' : 'auto',
      follows: [previous],
      content: `# ${spec.title}\n\nFrom ${doc.title} of ${parent.id}.\n`,
      created: now,
      updated: now,
      phase:
        spec.command === 'plan-task'
          ? 'planning'
          : spec.command === 'watch-pr'
            ? 'finalising'
            : 'executing',
      actionable: false,
    };
    tasks = { ...tasks, [id]: task };
    const promoted = { ...task, actionable: isActionable(task, tasks) };
    tasks = { ...tasks, [id]: promoted };
    events.push({ type: 'upsert', kind: 'task', entity: promoted });
    previous = id;
  }
  return events;
}
