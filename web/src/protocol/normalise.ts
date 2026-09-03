import type { Attention, AttentionKind } from './attention';
import type { Document } from './documents';
import type { Agent, AgentState } from './entities';
import type { ServerEvent } from './events';
import type { AgentId, AttentionId, DocumentId, RequestId } from './ids';
import type { ClientState } from './reducer';
import type { Question, TranscriptItem } from './transcript';

/**
 * Raw stream-json from the daemon. Loosely typed on purpose: the stream
 * carries plenty the UI has no opinion on, and every shape read here was
 * recorded in `tests/fixtures/agent_events/`.
 */
export interface RawStreamEvent {
  type: string;
  [key: string]: unknown;
}

type Dict = Record<string, unknown>;

export const QUESTION_TOOL = 'AskUserQuestion';
export const PLAN_TOOL = 'ExitPlanMode';

/** The request the agent is blocked on and what the UI made of it. */
export interface PendingContext {
  requestId: RequestId;
  /** The tool_use block the request belongs to; '' when the stream did not say. */
  toolUseId: string;
  tool: string;
  input: Dict;
  itemId: string;
  attentionId: AttentionId;
  documentId: DocumentId | null;
}

/**
 * What the normaliser has to remember between events for one agent: ids it
 * handed out, the tool calls still open, the pending request, and the last
 * thing the agent said (a plan review without a plan falls back to it).
 */
export interface NormaliseContext {
  agentId: AgentId;
  nextId: number;
  openToolCalls: Record<string, string>;
  pending: PendingContext | null;
  lastAssistantText: string;
  /** Tool uses the CLI refused by rule; their tool_result arrives as `denied`. */
  deniedToolUses: string[];
}

/** A fresh context, or one rebuilt from what the world already holds for the agent. */
export function contextForAgent(state: ClientState, agentId: AgentId): NormaliseContext {
  const agent = state.world.agents[agentId];
  const items = state.transcripts[agentId]?.items ?? [];
  const ctx: NormaliseContext = {
    agentId,
    nextId: items.length + 1,
    openToolCalls: {},
    pending: null,
    lastAssistantText: '',
    deniedToolUses: [],
  };
  for (const item of items) {
    if (item.type === 'tool_call' && (item.status === 'running' || item.status === 'pending')) {
      ctx.openToolCalls[item.toolUseId] = item.id;
    }
    if (item.type === 'message' && item.role === 'assistant') ctx.lastAssistantText = item.markdown;
  }
  const requestId = agent?.pendingRequestId;
  if (agent && requestId) {
    const item = [...items].reverse().find((i) => 'requestId' in i && i.requestId === requestId);
    const attention = Object.values(state.world.attention).find(
      (a) => a.clearedAt === null && a.requestId === requestId,
    );
    if (item) {
      ctx.pending = {
        requestId,
        toolUseId: '',
        tool:
          item.type === 'question'
            ? QUESTION_TOOL
            : item.type === 'plan_review'
              ? PLAN_TOOL
              : item.type === 'permission_request'
                ? item.tool
                : '',
        input:
          item.type === 'permission_request'
            ? item.input
            : item.type === 'question'
              ? { questions: item.questions }
              : {},
        itemId: item.id,
        attentionId: attention?.id ?? '',
        documentId: item.type === 'plan_review' ? item.documentId : null,
      };
    }
  }
  return ctx;
}

/** A transcript item without the fields the emitter fills in. */
type ItemBody = TranscriptItem extends infer T
  ? T extends TranscriptItem
    ? Omit<T, 'id' | 'ts'>
    : never
  : never;

export interface Normalised {
  events: ServerEvent[];
  ctx: NormaliseContext;
}

/**
 * One raw daemon event, as the events the UI wants. Pure. The state machine
 * follows `agent_model.apply_event`: a pending request outranks assistant
 * output, a control_response for the pending request ends the wait, a result
 * ends the turn idle.
 */
export function normaliseStreamEvent(
  state: ClientState,
  ctx: NormaliseContext,
  raw: RawStreamEvent,
  now: string,
): Normalised {
  const agent = state.world.agents[ctx.agentId];
  if (!agent) return { events: [], ctx };
  const out = new Emitter(state, agent, ctx, now);

  switch (raw.type) {
    case 'system':
      if (raw.subtype === 'init') {
        const sessionId = str(raw.session_id);
        const model = str(raw.model);
        out.append({ type: 'system', subtype: 'init', sessionId, model });
        out.agent({ session: sessionId || agent.session, model: model || agent.model });
      } else if (raw.subtype === 'permission_denied') {
        out.ctx.deniedToolUses = [...out.ctx.deniedToolUses, str(raw.tool_use_id)];
      }
      break;

    case 'user':
      for (const block of blocks(raw)) {
        if (block.type === 'text' && str(block.text)) {
          out.append({ type: 'message', role: 'user', markdown: str(block.text) });
        } else if (block.type === 'tool_result') {
          out.toolResult(block);
        }
      }
      break;

    case 'assistant': {
      for (const block of blocks(raw)) {
        if (block.type === 'text' && str(block.text)) {
          out.append({ type: 'message', role: 'assistant', markdown: str(block.text) });
          out.ctx.lastAssistantText = str(block.text);
          out.agent({ lastMessage: oneLine(str(block.text)) });
        } else if (block.type === 'tool_use') {
          const id = str(block.id);
          out.append(
            {
              type: 'tool_call',
              toolUseId: id,
              tool: str(block.name),
              input: dict(block.input),
              status: 'running',
            },
            id,
          );
          out.ctx.openToolCalls = { ...out.ctx.openToolCalls, [id]: id };
        }
      }
      if (!out.ctx.pending && agent.state !== 'processing') out.agent({ state: 'processing' });
      break;
    }

    case 'control_request': {
      const request = dict(raw.request);
      if (request.subtype !== 'can_use_tool') break;
      out.request(
        str(raw.request_id),
        str(request.tool_use_id),
        str(request.tool_name),
        dict(request.input),
        str(request.description),
      );
      break;
    }

    case 'control_response': {
      const response = dict(raw.response);
      const requestId = str(response.request_id);
      if (out.ctx.pending && requestId === out.ctx.pending.requestId) {
        out.response(dict(response.response));
      }
      break;
    }

    case 'result':
      out.append({
        type: 'turn_result',
        subtype: str(raw.subtype) || 'success',
        costUsd: num(raw.total_cost_usd),
        durationMs: num(raw.duration_ms),
      });
      out.endWait();
      out.agent({
        state: 'idle',
        costUsd: num(raw.total_cost_usd),
        session: str(raw.session_id) || agent.session,
      });
      break;

    default:
      break;
  }
  return out.done();
}

/** The events for an agent whose process has gone. Mirrors `mark_exited`. */
export function markExited(
  state: ClientState,
  ctx: NormaliseContext,
  exitCode: number,
  now: string,
): Normalised {
  const agent = state.world.agents[ctx.agentId];
  if (!agent) return { events: [], ctx };
  const out = new Emitter(state, agent, ctx, now);
  out.endWait();
  out.agent({ state: 'exited', exitCode, pendingRequestId: null, waitingOn: '' });
  if (exitCode !== 0) {
    out.raise('agent_exited', `Exited with code ${exitCode}`, null, null);
  }
  return out.done();
}

/**
 * The events for an exited agent that has come back under its own id.
 *
 * A resume keeps the agent id, so the row that returns names the agent the
 * world already holds. The exit is over: the code is cleared and the item that
 * asked someone to look at it goes with it. The inverse of `markExited`.
 *
 * `links` re-resolves the agent's task and worktree in the same event. A task
 * that arrived while the agent was gone would otherwise leave the revived agent
 * on screen with a stale link.
 */
export function reviveAgent(
  state: ClientState,
  ctx: NormaliseContext,
  rowState: Agent['state'],
  now: string,
  links: Pick<Agent, 'taskId' | 'project' | 'worktreeId' | 'phase'> | null = null,
): Normalised {
  const agent = state.world.agents[ctx.agentId];
  if (!agent) return { events: [], ctx };
  const out = new Emitter(state, agent, ctx, now);
  out.agent({ state: rowState, exitCode: null, ...(links ?? {}) });
  for (const item of Object.values(state.world.attention)) {
    if (item.kind === 'agent_exited' && item.agentId === ctx.agentId && item.clearedAt === null) {
      out.clear(item.id);
    }
  }
  return out.done();
}

/** Collects the events for one raw event and threads the context through. */
class Emitter {
  events: ServerEvent[] = [];
  ctx: NormaliseContext;
  private agentEntity: Agent;
  private agentDirty = false;

  /** Entities this batch created, so a later step in the batch can update them. */
  private local: { attention: Record<string, Attention>; documents: Record<string, Document> } = {
    attention: {},
    documents: {},
  };

  private state: ClientState;
  private now: string;

  constructor(state: ClientState, agent: Agent, ctx: NormaliseContext, now: string) {
    this.state = state;
    this.now = now;
    this.agentEntity = agent;
    this.ctx = { ...ctx };
  }

  newId(): string {
    const id = `${this.ctx.agentId}-${this.ctx.nextId}`;
    this.ctx.nextId += 1;
    return id;
  }

  append(item: ItemBody, id?: string): string {
    const itemId = id ?? this.newId();
    this.events.push({
      type: 'transcript.append',
      agentId: this.ctx.agentId,
      item: { ...item, id: itemId, ts: this.now } as TranscriptItem,
    });
    return itemId;
  }

  update(itemId: string, patch: Partial<TranscriptItem>) {
    this.events.push({ type: 'transcript.update', agentId: this.ctx.agentId, itemId, patch });
  }

  agent(patch: Partial<Agent>) {
    this.agentEntity = { ...this.agentEntity, ...patch };
    this.agentDirty = true;
  }

  toolResult(block: Dict) {
    const toolUseId = str(block.tool_use_id);
    const itemId = this.ctx.openToolCalls[toolUseId];
    if (!itemId) return;
    const denied = this.ctx.deniedToolUses.includes(toolUseId);
    if (denied) this.ctx.deniedToolUses = this.ctx.deniedToolUses.filter((id) => id !== toolUseId);
    const status = denied ? 'denied' : block.is_error ? 'error' : 'done';
    this.update(itemId, { status, output: resultText(block.content) });
    const open = { ...this.ctx.openToolCalls };
    delete open[toolUseId];
    this.ctx.openToolCalls = open;
  }

  request(requestId: RequestId, toolUseId: string, tool: string, input: Dict, description: string) {
    let itemId: string;
    let documentId: DocumentId | null = null;
    let kind: AttentionKind;
    let summary: string;
    let waitState: AgentState;
    if (tool === QUESTION_TOOL) {
      const questions = questionsOf(input);
      itemId = this.append({ type: 'question', requestId, questions });
      kind = 'question';
      summary = questions[0]?.question ?? tool;
      waitState = 'awaiting-question';
    } else if (tool === PLAN_TOOL) {
      const plan = str(input.plan);
      // A plan sent back for changes comes around again as the next version of
      // the same document, so its comments stay attached to their version.
      const previous = Object.values(this.state.world.documents).find(
        (d) =>
          d.agentId === this.ctx.agentId && d.kind === 'plan' && d.status === 'changes-requested',
      );
      documentId = previous?.id ?? this.newId();
      const doc: Document = {
        id: documentId,
        agentId: this.ctx.agentId,
        taskId: this.agentEntity.taskId,
        kind: 'plan',
        title: 'plan.md',
        markdown: plan || this.ctx.lastAssistantText,
        version: (previous?.version ?? 0) + 1,
        status: 'awaiting-review',
        source: {
          type: 'plan_review',
          requestId,
          planFilePath: plan ? str(input.planFilePath) : '',
        },
      };
      this.local.documents[documentId] = doc;
      this.events.push({ type: 'upsert', kind: 'document', entity: doc });
      itemId = this.append({ type: 'plan_review', requestId, documentId });
      kind = 'plan_review';
      summary = 'Plan awaiting review';
      waitState = 'awaiting-plan-review';
    } else {
      itemId = this.append({ type: 'permission_request', requestId, tool, input, description });
      kind = 'permission';
      summary = description || tool;
      waitState = 'awaiting-permission';
    }
    const attentionId = this.raise(kind, summary, requestId, documentId);
    this.ctx.pending = { requestId, toolUseId, tool, input, itemId, attentionId, documentId };
    this.agent({ state: waitState, pendingRequestId: requestId, waitingOn: summary });
  }

  response(payload: Dict) {
    const pending = this.ctx.pending!;
    const allow = payload.behavior === 'allow';
    if (pending.tool === QUESTION_TOOL) {
      const answers = dict(dict(payload.updatedInput).answers);
      if (Object.keys(answers).length > 0) {
        this.update(pending.itemId, { answers: answers as Record<string, string> });
      }
    } else if (pending.tool === PLAN_TOOL) {
      this.update(pending.itemId, {
        decision: allow ? 'approve' : 'deny',
        ...(allow ? {} : { reason: str(payload.message) }),
      });
      if (pending.documentId)
        this.documentStatus(pending.documentId, allow ? 'approved' : 'changes-requested');
    } else {
      this.update(pending.itemId, {
        decision: allow ? 'allow' : 'deny',
        ...(allow ? {} : { reason: str(payload.message) }),
      });
    }
    this.endWait();
    this.agent({ state: 'processing', pendingRequestId: null, waitingOn: '' });
  }

  /** Clear the pending request and its attention item, if any. */
  endWait() {
    const pending = this.ctx.pending;
    if (!pending) return;
    this.clear(pending.attentionId);
    this.ctx.pending = null;
  }

  raise(
    kind: AttentionKind,
    summary: string,
    requestId: RequestId | null,
    documentId: DocumentId | null,
  ): AttentionId {
    const id = `att-${this.newId()}`;
    const item: Attention = {
      id,
      kind,
      agentId: this.ctx.agentId,
      taskId: this.agentEntity.taskId,
      documentId,
      requestId,
      summary,
      raisedAt: this.now,
      clearedAt: null,
    };
    this.local.attention[id] = item;
    this.events.push({ type: 'upsert', kind: 'attention', entity: item });
    return id;
  }

  clear(attentionId: AttentionId) {
    const item = this.local.attention[attentionId] ?? this.state.world.attention[attentionId];
    if (!item || item.clearedAt !== null) return;
    const cleared = { ...item, clearedAt: this.now };
    this.local.attention[attentionId] = cleared;
    this.events.push({ type: 'upsert', kind: 'attention', entity: cleared });
  }

  documentStatus(documentId: DocumentId, status: Document['status']) {
    const doc = this.local.documents[documentId] ?? this.state.world.documents[documentId];
    if (!doc) return;
    const next = { ...doc, status };
    this.local.documents[documentId] = next;
    this.events.push({ type: 'upsert', kind: 'document', entity: next });
  }

  done(): Normalised {
    if (this.agentDirty) {
      this.events.push({ type: 'upsert', kind: 'agent', entity: this.agentEntity });
    }
    return { events: this.events, ctx: this.ctx };
  }
}

function blocks(raw: RawStreamEvent): Dict[] {
  const content = dict(raw.message).content;
  return Array.isArray(content)
    ? content.filter((b): b is Dict => !!b && typeof b === 'object')
    : [];
}

function resultText(content: unknown): string {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .map((b) => (b && typeof b === 'object' && 'text' in b ? str((b as Dict).text) : ''))
      .filter(Boolean)
      .join('\n');
  }
  return '';
}

function questionsOf(input: Dict): Question[] {
  const raw = Array.isArray(input.questions) ? input.questions : [];
  return raw
    .filter((q): q is Dict => !!q && typeof q === 'object')
    .map((q) => ({
      question: str(q.question),
      header: str(q.header),
      multiSelect: !!q.multiSelect,
      options: (Array.isArray(q.options) ? q.options : [])
        .filter((o): o is Dict => !!o && typeof o === 'object')
        .map((o) => ({ label: str(o.label), description: str(o.description) })),
    }));
}

function oneLine(text: string, limit = 60): string {
  return text.split(/\s+/).join(' ').slice(0, limit);
}

function str(v: unknown): string {
  return typeof v === 'string' ? v : '';
}
function num(v: unknown): number {
  return typeof v === 'number' ? v : 0;
}
function dict(v: unknown): Dict {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Dict) : {};
}
