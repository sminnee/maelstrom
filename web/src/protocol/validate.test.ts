import { describe, expect, it } from 'vitest';
import { validateCommand } from './validate';
import { makeAgent, makeDeskEntry, makeDocument, makeTask, worldWith } from '../test/fixtures';

const waitingForPlan = makeAgent({
  id: 'agent-1',
  state: 'awaiting-plan-review',
  pendingRequestId: 'req-1',
});

describe('validateCommand', () => {
  it('reports unknown_id for an agent that is not in the world', () => {
    const world = worldWith({ agents: [waitingForPlan] });
    expect(
      validateCommand(world, { type: 'agent.approve', agentId: 'ghost', requestId: 'req-1' }),
    ).toMatchObject({ code: 'unknown_id' });
  });

  it('reports agent_exited when the agent has gone', () => {
    const world = worldWith({ agents: [makeAgent({ state: 'exited', exitCode: 0 })] });
    expect(
      validateCommand(world, { type: 'agent.say', agentId: 'agent-1', text: 'hi' }),
    ).toMatchObject({ code: 'agent_exited' });
  });

  it('allows a resume of an agent that has exited', () => {
    const world = worldWith({ agents: [makeAgent({ state: 'exited', exitCode: 1 })] });
    expect(validateCommand(world, { type: 'agent.resume', agentId: 'agent-1' })).toBeNull();
  });

  it('refuses a resume of an agent that is still running', () => {
    const world = worldWith({ agents: [makeAgent({ state: 'idle' })] });
    expect(validateCommand(world, { type: 'agent.resume', agentId: 'agent-1' })).toMatchObject({
      code: 'invalid',
    });
  });

  it('reports unknown_id for a resume of an agent it does not know', () => {
    expect(
      validateCommand(worldWith({}), { type: 'agent.resume', agentId: 'ghost' }),
    ).toMatchObject({ code: 'unknown_id' });
  });

  it('reports not_waiting when the agent has no pending request', () => {
    const world = worldWith({ agents: [makeAgent({ state: 'processing' })] });
    expect(
      validateCommand(world, { type: 'agent.approve', agentId: 'agent-1', requestId: 'req-1' }),
    ).toMatchObject({ code: 'not_waiting' });
  });

  it('reports stale_request when the request id is not the pending one', () => {
    const world = worldWith({ agents: [waitingForPlan] });
    expect(
      validateCommand(world, { type: 'agent.approve', agentId: 'agent-1', requestId: 'old' }),
    ).toMatchObject({ code: 'stale_request' });
  });

  it('reports wrong_wait_kind when answering a permission request', () => {
    const world = worldWith({
      agents: [makeAgent({ state: 'awaiting-permission', pendingRequestId: 'req-2' })],
    });
    expect(
      validateCommand(world, {
        type: 'agent.answer',
        agentId: 'agent-1',
        requestId: 'req-2',
        answers: { 'Which?': 'A' },
      }),
    ).toMatchObject({ code: 'wrong_wait_kind' });
  });

  it('reports stale_version when approving an older document version', () => {
    const world = worldWith({ documents: [makeDocument({ version: 3 })] });
    expect(
      validateCommand(world, { type: 'document.approve', documentId: 'doc-1', version: 2 }),
    ).toMatchObject({ code: 'stale_version' });
  });

  it('reports invalid for an empty message', () => {
    const world = worldWith({ agents: [makeAgent()] });
    expect(
      validateCommand(world, { type: 'agent.say', agentId: 'agent-1', text: '   ' }),
    ).toMatchObject({ code: 'invalid' });
  });

  it('reports invalid for a change request with no summary and no comments', () => {
    const world = worldWith({ documents: [makeDocument()] });
    expect(
      validateCommand(world, {
        type: 'document.requestChanges',
        documentId: 'doc-1',
        version: 1,
        summary: '',
      }),
    ).toMatchObject({ code: 'invalid' });
  });

  it('reports invalid for resolving a comment twice', () => {
    const world = worldWith({ documents: [makeDocument()] });
    world.comments['c1'] = {
      id: 'c1',
      documentId: 'doc-1',
      version: 1,
      author: 'user',
      anchor: { quote: 'x', prefix: '', suffix: '', start: 0, end: 1 },
      body: 'fix',
      resolved: true,
      createdAt: '2026-09-01T00:00:00Z',
    };
    expect(validateCommand(world, { type: 'comment.resolve', commentId: 'c1' })).toMatchObject({
      code: 'invalid',
    });
  });

  it('reports invalid for launching a task that is not actionable', () => {
    const world = worldWith({ tasks: [makeTask({ actionable: false })] });
    expect(validateCommand(world, { type: 'agent.launch', taskId: 'NORT-7' })).toMatchObject({
      code: 'invalid',
    });
  });

  it('reports invalid for approving a document that is not awaiting review', () => {
    const world = worldWith({ documents: [makeDocument({ status: 'approved' })] });
    expect(
      validateCommand(world, { type: 'document.approve', documentId: 'doc-1', version: 1 }),
    ).toMatchObject({ code: 'invalid' });
  });

  it('reports unknown_id for adding a task that is not in the world to the desk', () => {
    expect(validateCommand(worldWith({}), { type: 'desk.add', id: 'task:NORT-7' })).toMatchObject({
      code: 'unknown_id',
    });
  });

  it('reports unknown_id for adding an agent that is not in the world to the desk', () => {
    expect(validateCommand(worldWith({}), { type: 'desk.add', id: 'agent:ghost' })).toMatchObject({
      code: 'unknown_id',
    });
  });

  it('reports unknown_id for adding an id that carries no kind', () => {
    const world = worldWith({ tasks: [makeTask()] });
    expect(validateCommand(world, { type: 'desk.add', id: 'NORT-7' })).toMatchObject({
      code: 'unknown_id',
    });
  });

  it('reports unknown_id for removing a task that is not on the desk', () => {
    const world = worldWith({ tasks: [makeTask()] });
    expect(validateCommand(world, { type: 'desk.remove', id: 'task:NORT-7' })).toMatchObject({
      code: 'unknown_id',
    });
  });

  it('accepts adding a task that is on the desk already', () => {
    const world = worldWith({ tasks: [makeTask()], desk: [makeDeskEntry()] });
    expect(validateCommand(world, { type: 'desk.add', id: 'task:NORT-7' })).toBeNull();
  });

  it('accepts adding a free agent to the desk', () => {
    const world = worldWith({ agents: [makeAgent({ id: 'agent-1' })] });
    expect(validateCommand(world, { type: 'desk.add', id: 'agent:agent-1' })).toBeNull();
  });

  it('accepts a well-formed approve of the pending request', () => {
    const world = worldWith({ agents: [waitingForPlan] });
    expect(
      validateCommand(world, { type: 'agent.approve', agentId: 'agent-1', requestId: 'req-1' }),
    ).toBeNull();
  });
});
