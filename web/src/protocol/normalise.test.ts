import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { contextForAgent, normaliseStreamEvent, type RawStreamEvent } from './normalise';
import { applyEvent, initialClientState, type ClientState } from './reducer';
import { makeAgent, makeDocument, worldWith } from '../test/fixtures';

// vitest runs from web/, and the recorded daemon streams live beside the Python tests.
const FIXTURES = resolve(process.cwd(), '../tests/fixtures/agent_events');

function readFixture(name: string): RawStreamEvent[] {
  return readFileSync(resolve(FIXTURES, name), 'utf8')
    .split('\n')
    .filter(Boolean)
    .map((line) => JSON.parse(line) as RawStreamEvent);
}

/** Replay a fixture through normalise and the reducer, as a real backend would. */
function replay(name: string, opts: { stopBeforeControlResponse?: boolean } = {}): ClientState {
  const agent = makeAgent({ id: 'ag1', state: 'idle' });
  let state = applyEvent(initialClientState(), {
    type: 'snapshot',
    world: worldWith({ agents: [agent] }),
    transcripts: {},
  });
  let ctx = contextForAgent(state, 'ag1');
  for (const raw of readFixture(name)) {
    if (opts.stopBeforeControlResponse && raw.type === 'control_response' && ctx.pending) break;
    const out = normaliseStreamEvent(state, ctx, raw, '2026-09-01T00:00:00Z');
    ctx = out.ctx;
    for (const event of out.events) state = applyEvent(state, event);
  }
  return state;
}

const types = (state: ClientState) => (state.transcripts['ag1']?.items ?? []).map((i) => i.type);
const agentOf = (state: ClientState) => state.world.agents['ag1']!;
const openAttention = (state: ClientState) =>
  Object.values(state.world.attention).filter((a) => a.clearedAt === null);

describe('normaliseStreamEvent replays the recorded daemon streams', () => {
  it('every fixture applies through the reducer without error', () => {
    const names = readdirSync(FIXTURES).filter((n) => n.endsWith('.jsonl'));
    expect(names.length).toBeGreaterThan(0);
    for (const name of names) expect(() => replay(name)).not.toThrow();
  });

  it('a completed turn ends idle with the cost and one result line', () => {
    const state = replay('normal-turn.jsonl');
    expect(types(state)).toEqual(['system', 'message', 'message', 'turn_result']);
    expect(agentOf(state)).toMatchObject({ state: 'idle', costUsd: 0.1495855 });
    expect(state.transcripts['ag1']?.items[0]).toMatchObject({
      type: 'system',
      sessionId: '029ed263-b318-4d4e-a661-32f9c9f23f19',
    });
  });

  it('plan review with a plan yields a plan document awaiting review and one attention item', () => {
    const state = replay('plan-review-with-plan.jsonl');
    expect(agentOf(state).state).toBe('awaiting-plan-review');
    const docs = Object.values(state.world.documents);
    expect(docs).toHaveLength(1);
    expect(docs[0]).toMatchObject({ kind: 'plan', status: 'awaiting-review' });
    expect(docs[0]?.markdown).toMatch(/^# Create hello.txt/);
    expect(docs[0]?.source).toMatchObject({
      type: 'plan_review',
      requestId: '9df2f603-da86-44cf-ac99-4e102c7f7add',
    });
    expect(openAttention(state)).toHaveLength(1);
    expect(openAttention(state)[0]).toMatchObject({ kind: 'plan_review', documentId: docs[0]?.id });
    const last = state.transcripts['ag1']?.items.at(-1);
    expect(last).toMatchObject({ type: 'plan_review', documentId: docs[0]?.id });
  });

  it('plan review without a plan takes the last message as the plan text', () => {
    const state = replay('plan-review.jsonl', { stopBeforeControlResponse: true });
    expect(agentOf(state).state).toBe('awaiting-plan-review');
    const doc = Object.values(state.world.documents)[0];
    expect(doc?.markdown.length).toBeGreaterThan(20);
    expect(doc?.source).toMatchObject({ type: 'plan_review', planFilePath: '' });
  });

  it('an approved plan review resumes the agent and approves the document', () => {
    const state = replay('plan-review.jsonl');
    expect(agentOf(state)).toMatchObject({ state: 'idle', pendingRequestId: null });
    expect(Object.values(state.world.documents)[0]?.status).toBe('approved');
    expect(openAttention(state)).toHaveLength(0);
    const review = state.transcripts['ag1']?.items.find((i) => i.type === 'plan_review');
    expect(review).toMatchObject({ decision: 'approve' });
  });

  it('an unanswered question leaves the agent awaiting a question, not a permission', () => {
    const state = replay('question-unanswered.jsonl', { stopBeforeControlResponse: true });
    expect(agentOf(state)).toMatchObject({
      state: 'awaiting-question',
      pendingRequestId: '2ba1273d-d878-4923-ba21-31faa1067613',
      waitingOn: 'Which colour do you prefer?',
    });
    expect(openAttention(state)[0]).toMatchObject({ kind: 'question' });
    const q = state.transcripts['ag1']?.items.find((i) => i.type === 'question');
    expect(q).toMatchObject({ questions: [{ question: 'Which colour do you prefer?' }] });
    expect(q && 'answers' in q ? q.answers : undefined).toBeUndefined();
  });

  it('an answered question records the answers on the question item', () => {
    const state = replay('question-answered.jsonl');
    const q = state.transcripts['ag1']?.items.find((i) => i.type === 'question');
    expect(q).toMatchObject({ answers: { 'Which colour do you prefer?': 'Green' } });
    expect(agentOf(state).state).toBe('idle');
  });

  it('a permission request awaits permission and its allow is recorded', () => {
    const waiting = replay('permission-request.jsonl', { stopBeforeControlResponse: true });
    expect(agentOf(waiting).state).toBe('awaiting-permission');
    expect(openAttention(waiting)[0]).toMatchObject({ kind: 'permission' });
    const done = replay('permission-request.jsonl');
    const req = done.transcripts['ag1']?.items.find((i) => i.type === 'permission_request');
    expect(req).toMatchObject({ tool: 'WebFetch', decision: 'allow' });
    expect(agentOf(done).state).toBe('idle');
  });

  it('a denied tool call ends denied and the agent is not left waiting', () => {
    const state = replay('permission-denied.jsonl');
    const call = state.transcripts['ag1']?.items.find((i) => i.type === 'tool_call');
    expect(call).toMatchObject({ tool: 'Bash', status: 'denied' });
    expect(agentOf(state).state).toBe('idle');
  });

  it('a tool_use and its tool_result merge into one tool_call item', () => {
    const state = replay('plan-review.jsonl');
    const calls = (state.transcripts['ag1']?.items ?? []).filter((i) => i.type === 'tool_call');
    expect(calls.length).toBeGreaterThan(2);
    expect(calls.every((c) => c.status === 'done' || c.status === 'error')).toBe(true);
    const errored = calls.find((c) => c.status === 'error');
    expect(errored?.output).toMatch(/EPERM/);
  });
});

describe('a plan sent back for changes', () => {
  it('comes around as the next version of the same document', () => {
    const doc = makeDocument({
      id: 'doc-1',
      agentId: 'ag1',
      taskId: 'NORT-7',
      version: 1,
      status: 'changes-requested',
    });
    const agent = makeAgent({ id: 'ag1', state: 'processing' });
    let state = applyEvent(initialClientState(), {
      type: 'snapshot',
      world: worldWith({ agents: [agent], documents: [doc] }),
      transcripts: {},
    });
    const ctx = contextForAgent(state, 'ag1');
    const out = normaliseStreamEvent(
      state,
      ctx,
      {
        type: 'control_request',
        request_id: 'req-2',
        request: {
          subtype: 'can_use_tool',
          tool_name: 'ExitPlanMode',
          input: { plan: '# Revised', planFilePath: '/p.md' },
          tool_use_id: 'toolu_2',
        },
      },
      '2026-09-01T00:00:00Z',
    );
    for (const event of out.events) state = applyEvent(state, event);
    const docs = Object.values(state.world.documents);
    expect(docs).toHaveLength(1);
    expect(docs[0]).toMatchObject({
      id: 'doc-1',
      version: 2,
      status: 'awaiting-review',
      markdown: '# Revised',
    });
  });
});
