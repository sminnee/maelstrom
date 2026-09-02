import type { Command, ResultMap } from '../../protocol/commands';
import type { ServerEvent } from '../../protocol/events';
import type { ClientState } from '../../protocol/reducer';
import type { Agent } from '../../protocol/entities';
import type { Document } from '../../protocol/documents';
import type { Attention } from '../../protocol/attention';

export interface Consequence {
  events: ServerEvent[];
  result: ResultMap[Command['type']];
}

let counter = 0;
const nextId = (prefix: string) => `${prefix}-${(counter += 1)}`;

/**
 * What the world does because of a validated command. Pure: returns the
 * events, and the store applies them. A real backend does the same work by
 * writing to the daemon, the notebook or GitHub, then reporting the result.
 */
export function applyCommand(state: ClientState, cmd: Command, now: string): Consequence {
  const { world } = state;
  switch (cmd.type) {
    case 'agent.approve':
    case 'agent.deny':
    case 'agent.answer': {
      const agent = world.agents[cmd.agentId]!;
      const events: ServerEvent[] = [];
      const patch =
        cmd.type === 'agent.answer'
          ? { answers: cmd.answers }
          : agent.state === 'awaiting-plan-review'
            ? { decision: cmd.type === 'agent.approve' ? 'approve' : 'deny' }
            : {
                decision: cmd.type === 'agent.approve' ? 'allow' : 'deny',
                ...(cmd.type === 'agent.deny' ? { reason: cmd.reason } : {}),
              };
      const item = pendingItem(state, agent);
      if (item) {
        events.push({ type: 'transcript.update', agentId: agent.id, itemId: item, patch });
      }
      if (cmd.type === 'agent.deny') {
        events.push({
          type: 'transcript.append',
          agentId: agent.id,
          item: { id: nextId('msg'), ts: now, type: 'message', role: 'user', markdown: cmd.reason },
        });
      }
      for (const doc of documentsFor(world.documents, cmd.requestId)) {
        events.push({
          type: 'upsert',
          kind: 'document',
          entity: {
            ...doc,
            status: cmd.type === 'agent.approve' ? 'approved' : 'changes-requested',
          },
        });
      }
      for (const att of openAttention(world.attention, agent.id)) {
        events.push({ type: 'upsert', kind: 'attention', entity: { ...att, clearedAt: now } });
      }
      events.push({
        type: 'upsert',
        kind: 'agent',
        entity: { ...agent, state: 'processing', waitingOn: '', pendingRequestId: null },
      });
      return { events, result: {} };
    }
    case 'agent.say': {
      const agent = world.agents[cmd.agentId]!;
      const events: ServerEvent[] = [
        {
          type: 'transcript.append',
          agentId: agent.id,
          item: { id: nextId('msg'), ts: now, type: 'message', role: 'user', markdown: cmd.text },
        },
      ];
      if (agent.state === 'idle') {
        events.push({ type: 'upsert', kind: 'agent', entity: { ...agent, state: 'processing' } });
      }
      return { events, result: {} };
    }
    case 'agent.stop': {
      const agent = world.agents[cmd.agentId]!;
      return {
        events: [
          {
            type: 'upsert',
            kind: 'agent',
            entity: {
              ...agent,
              state: 'exited',
              exitCode: 0,
              pendingRequestId: null,
              waitingOn: '',
            },
          },
          ...openAttention(world.attention, agent.id).map((att): ServerEvent => ({
            type: 'upsert',
            kind: 'attention',
            entity: { ...att, clearedAt: now },
          })),
        ],
        result: {},
      };
    }
    case 'agent.launch': {
      const task = world.tasks[cmd.taskId]!;
      const worktree =
        Object.values(world.worktrees).find((w) => w.branch === task.branch && !w.isClosed) ??
        Object.values(world.worktrees).find((w) => w.project === task.project);
      const agent: Agent = {
        id: nextId('ag'),
        state: 'processing',
        session: '',
        cwd: worktree?.path ?? '',
        model: cmd.model ?? 'claude-opus-5',
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
      return {
        events: [
          {
            type: 'upsert',
            kind: 'task',
            entity: { ...task, status: 'in-progress', updated: now },
          },
          { type: 'upsert', kind: 'agent', entity: agent },
          {
            type: 'transcript.append',
            agentId: agent.id,
            item: {
              id: nextId('sys'),
              ts: now,
              type: 'system',
              subtype: 'init',
              sessionId: `sess-${agent.id}`,
              model: agent.model,
            },
          },
        ],
        result: { agentId: agent.id },
      };
    }
    case 'document.approve':
    case 'document.requestChanges': {
      const doc = world.documents[cmd.documentId]!;
      const agent = world.agents[doc.agentId];
      const approved = cmd.type === 'document.approve';
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
      if (agent && agent.state !== 'exited') {
        const item = pendingItem(state, agent);
        if (item && agent.pendingRequestId) {
          events.push({
            type: 'transcript.update',
            agentId: agent.id,
            itemId: item,
            patch: { decision: approved ? 'approve' : 'deny' },
          });
        }
        if (!approved) {
          const unresolved = Object.values(world.comments).filter(
            (c) => c.documentId === doc.id && c.version === doc.version && !c.resolved,
          );
          const body = [cmd.summary, ...unresolved.map((c) => `> ${c.anchor.quote}\n\n${c.body}`)]
            .filter(Boolean)
            .join('\n\n');
          events.push({
            type: 'transcript.append',
            agentId: agent.id,
            item: {
              id: nextId('msg'),
              ts: now,
              type: 'message',
              role: 'user',
              markdown: `Changes requested on ${doc.title} v${doc.version}:\n\n${body}`,
            },
          });
        }
        events.push({
          type: 'upsert',
          kind: 'agent',
          entity: { ...agent, state: 'processing', waitingOn: '', pendingRequestId: null },
        });
      }
      return { events, result: {} };
    }
    case 'comment.add': {
      const id = nextId('cmt');
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
        result: { commentId: id },
      };
    }
    case 'comment.resolve': {
      const comment = world.comments[cmd.commentId]!;
      return {
        events: [{ type: 'upsert', kind: 'comment', entity: { ...comment, resolved: true } }],
        result: {},
      };
    }
    case 'task.create': {
      const id = `${cmd.project.slice(0, 4).toUpperCase()}-${100 + (counter += 1)}`;
      const title = cmd.draft.split('\n')[0]?.replace(/^#\s*/, '') || 'Untitled';
      return {
        events: [
          {
            type: 'upsert',
            kind: 'task',
            entity: {
              id,
              project: cmd.project,
              title,
              status: 'todo',
              command: '',
              mode: 'auto',
              branch: `feat/${id.toLowerCase()}`,
              parent: '',
              follows: [],
              priority: 'normal',
              model: '',
              base: '',
              content: cmd.draft,
              steps: [],
              log: [],
              created: now,
              updated: now,
              phase: 'executing',
              actionable: true,
            },
          },
        ],
        result: { taskId: id },
      };
    }
    case 'shaping.start': {
      const id = `${cmd.project.slice(0, 4).toUpperCase()}-${100 + (counter += 1)}`;
      const title = cmd.brief.split('\n')[0]?.slice(0, 60) || 'Shaping';
      const taskEvents = applyCommand(
        state,
        { type: 'task.create', project: cmd.project, draft: `# ${title}\n\n${cmd.brief}` },
        now,
      );
      const created = taskEvents.events[0];
      if (created?.type !== 'upsert' || created.kind !== 'task') throw new Error('unreachable');
      const shaped = {
        ...created.entity,
        id,
        command: 'shape',
        phase: 'shaping' as const,
        mode: 'normal' as const,
      };
      const next: ClientState = {
        ...state,
        world: { ...world, tasks: { ...world.tasks, [id]: shaped } },
      };
      const launch = applyCommand(next, { type: 'agent.launch', taskId: id }, now);
      return {
        events: [{ type: 'upsert', kind: 'task', entity: shaped }, ...launch.events],
        result: { agentId: (launch.result as { agentId: string }).agentId, taskId: id },
      };
    }
  }
}

/** The transcript item carrying the agent's pending request, if any. */
function pendingItem(state: ClientState, agent: Agent): string | null {
  if (!agent.pendingRequestId) return null;
  const items = state.transcripts[agent.id]?.items ?? [];
  for (let i = items.length - 1; i >= 0; i -= 1) {
    const item = items[i]!;
    if ('requestId' in item && item.requestId === agent.pendingRequestId) return item.id;
  }
  return null;
}

function documentsFor(documents: Record<string, Document>, requestId: string): Document[] {
  return Object.values(documents).filter(
    (d) =>
      d.status === 'awaiting-review' &&
      d.source.type === 'plan_review' &&
      d.source.requestId === requestId,
  );
}

function openAttention(attention: Record<string, Attention>, agentId: string): Attention[] {
  return Object.values(attention).filter((a) => a.clearedAt === null && a.agentId === agentId);
}
