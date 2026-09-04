import { readdirSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { contextForAgent, markExited, normaliseStreamEvent, reviveAgent } from './normalise';
import { applyEvent, initialClientState, type ClientState } from './reducer';
import { makeAgent, makeDocument, worldWith } from '../test/fixtures';
import {
  FIXTURES,
  goldenOf,
  readGolden,
  replayFixture as replay,
  writeGolden,
} from '../test/replayFixture';

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

  it('every fixture replays to its golden, which the Python normaliser is held to', () => {
    // `UPDATE_GOLDEN=1 pnpm test` re-records; see tests/test_orchestrator_normalise.py.
    const names = readdirSync(FIXTURES).filter((n) => n.endsWith('.jsonl'));
    for (const name of names) {
      writeGolden(name);
      expect(goldenOf(replay(name)), name).toEqual(readGolden(name));
    }
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

describe('a wait that ends without an answer', () => {
  /** The turn's own result, as the daemon sends it once the tool has run. */
  const RESULT = {
    type: 'result',
    subtype: 'success',
    total_cost_usd: 0.25,
    duration_ms: 1200,
    session_id: 'sess-1',
  };

  /** Replay `name` up to its pending request, then end the turn on it. */
  function endedMidWait(name: string, opts: { stopBeforeControlResponse?: boolean } = {}) {
    let state = replay(name, opts);
    const ctx = contextForAgent(state, 'ag1');
    const out = normaliseStreamEvent(state, ctx, RESULT, '2026-09-01T00:00:00Z');
    for (const event of out.events) state = applyEvent(state, event);
    return state;
  }

  const itemOf = (state: ClientState, type: string) =>
    (state.transcripts['ag1']?.items ?? []).find((i) => i.type === type)!;

  it('marks a permission request stale and takes it off the agent', () => {
    const state = endedMidWait('permission-request.jsonl', { stopBeforeControlResponse: true });
    const request = itemOf(state, 'permission_request');
    expect(request).toMatchObject({ stale: true });
    expect(request).not.toHaveProperty('decision');
    expect(agentOf(state)).toMatchObject({
      state: 'idle',
      pendingRequestId: null,
      waitingOn: '',
    });
    expect(openAttention(state)).toHaveLength(0);
  });

  it('marks a question stale without inventing answers', () => {
    const state = endedMidWait('question-unanswered.jsonl', { stopBeforeControlResponse: true });
    const question = itemOf(state, 'question');
    expect(question).toMatchObject({ stale: true });
    expect(question).not.toHaveProperty('answers');
    expect(agentOf(state)).toMatchObject({ pendingRequestId: null, waitingOn: '' });
  });

  it('marks a plan review stale and takes its plan out of review', () => {
    const state = endedMidWait('plan-review-with-plan.jsonl');
    const review = itemOf(state, 'plan_review');
    expect(review).toMatchObject({ stale: true });
    expect(review).not.toHaveProperty('decision');
    expect(Object.values(state.world.documents)[0]).toMatchObject({ status: 'stale' });
    expect(agentOf(state)).toMatchObject({ pendingRequestId: null, waitingOn: '' });
  });

  it('marks the open item stale when the agent exits mid-wait', () => {
    let state = replay('question-unanswered.jsonl', { stopBeforeControlResponse: true });
    const out = markExited(state, contextForAgent(state, 'ag1'), 1, '2026-09-01T00:00:00Z');
    for (const event of out.events) state = applyEvent(state, event);
    expect(itemOf(state, 'question')).toMatchObject({ stale: true });
    expect(agentOf(state)).toMatchObject({
      state: 'exited',
      pendingRequestId: null,
      waitingOn: '',
    });
    expect(openAttention(state).map((a) => a.kind)).toEqual(['agent_exited']);
  });

  it('marks a request stale when the user interrupts the wait', () => {
    const state = replay('interrupt-while-waiting.jsonl');
    const bash = (state.transcripts['ag1']?.items ?? []).find(
      (i) => i.type === 'permission_request' && i.tool === 'Bash',
    )!;
    expect(bash).toMatchObject({ stale: true });
    expect(bash).not.toHaveProperty('decision');
  });

  it('leaves an answered request alone', () => {
    const permission = itemOf(replay('permission-request.jsonl'), 'permission_request');
    expect(permission).toMatchObject({ decision: 'allow' });
    expect(permission).not.toHaveProperty('stale');
    const question = itemOf(replay('question-answered.jsonl'), 'question');
    expect(question).toMatchObject({ answers: { 'Which colour do you prefer?': 'Green' } });
    expect(question).not.toHaveProperty('stale');
  });
});

describe('reviveAgent', () => {
  /** An agent that has crashed, with the attention item its exit raised. */
  function exited(): ClientState {
    let state = initialClientState();
    state = applyEvent(state, {
      type: 'upsert',
      kind: 'agent',
      entity: makeAgent({ id: 'ag1', state: 'processing' }),
    });
    const out = markExited(state, contextForAgent(state, 'ag1'), 1, '2026-09-01T00:00:00Z');
    for (const event of out.events) state = applyEvent(state, event);
    return state;
  }

  it('clears the exit code so the agent reads as live again', () => {
    let state = exited();
    const out = reviveAgent(state, contextForAgent(state, 'ag1'), 'idle', '2026-09-01T00:01:00Z');
    for (const event of out.events) state = applyEvent(state, event);
    expect(state.world.agents['ag1']).toMatchObject({ state: 'idle', exitCode: null });
  });

  it('clears the attention item the exit raised', () => {
    let state = exited();
    const out = reviveAgent(state, contextForAgent(state, 'ag1'), 'idle', '2026-09-01T00:01:00Z');
    for (const event of out.events) state = applyEvent(state, event);
    const items = Object.values(state.world.attention).filter((a) => a.kind === 'agent_exited');
    expect(items).toHaveLength(1);
    expect(items[0]!.clearedAt).not.toBeNull();
  });

  it('does nothing for an agent the world does not hold', () => {
    const state = initialClientState();
    const out = reviveAgent(state, contextForAgent(state, 'ghost'), 'idle', '2026-09-01T00:00:00Z');
    expect(out.events).toEqual([]);
  });
});
