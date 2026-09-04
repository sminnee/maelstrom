import { deskIdForTask } from '../protocol/deskId';
import { describe, expect, it } from 'vitest';
import { createFakeBackend } from './createFakeBackend';
import { seedWorld } from './scenarios/seedWorld';
import type { TaskEdit } from '../protocol/commands';
import type { EventFrame } from '../protocol/events';

function collect(backend: { subscribe(l: (f: EventFrame) => void): () => void }) {
  const frames: EventFrame[] = [];
  backend.subscribe((f) => frames.push(f));
  return frames;
}

describe('the fake backend honours the Backend contract', () => {
  it('connect delivers a snapshot of the seed world first', async () => {
    const backend = createFakeBackend({ seed: 1, autoplay: false });
    const frames = collect(backend);
    await backend.connect();
    expect(frames[0]?.event.type).toBe('snapshot');
    const first = frames[0]?.event;
    if (first?.type !== 'snapshot') throw new Error('expected snapshot');
    expect(first.world).toEqual(seedWorld().world);
    expect(frames[0]?.seq).toBeGreaterThan(0);
  });

  it('a command is acked and its consequences arrive as events', async () => {
    const backend = createFakeBackend({ seed: 1, autoplay: false });
    const frames = collect(backend);
    await backend.connect();
    const snapshot = frames[0]?.event;
    if (snapshot?.type !== 'snapshot') throw new Error('expected snapshot');
    const waiting = Object.values(snapshot.world.agents).find(
      (a) => a.state === 'awaiting-plan-review',
    );
    if (!waiting?.pendingRequestId) throw new Error('seed has no plan review');

    const reply = await backend.command({
      type: 'agent.approve',
      agentId: waiting.id,
      requestId: waiting.pendingRequestId,
    });
    expect(reply.ok).toBe(true);
    const upsert = frames.find(
      (f) =>
        f.event.type === 'upsert' && f.event.kind === 'agent' && f.event.entity.id === waiting.id,
    );
    expect(upsert?.event).toMatchObject({
      entity: { state: 'processing', pendingRequestId: null },
    });
    expect(frames.every((f, i) => i === 0 || f.seq > (frames[i - 1]?.seq ?? 0))).toBe(true);
  });

  it('a refused command replies with an error and emits no event', async () => {
    const backend = createFakeBackend({ seed: 1, autoplay: false });
    const frames = collect(backend);
    await backend.connect();
    const before = frames.length;
    const reply = await backend.command({
      type: 'agent.approve',
      agentId: 'nobody',
      requestId: 'x',
    });
    expect(reply).toMatchObject({ ok: false, error: { code: 'unknown_id' } });
    expect(frames.length).toBe(before);
  });

  it('a command refused for state reasons emits no event either', async () => {
    const backend = createFakeBackend({ seed: 1, autoplay: false });
    const frames = collect(backend);
    await backend.connect();
    const snapshot = frames[0]?.event;
    if (snapshot?.type !== 'snapshot') throw new Error('expected snapshot');
    const asking = Object.values(snapshot.world.agents).find(
      (a) => a.state === 'awaiting-question',
    );
    if (!asking?.pendingRequestId) throw new Error('seed has no question');
    const before = frames.length;
    const reply = await backend.command({
      type: 'agent.approve',
      agentId: asking.id,
      requestId: asking.pendingRequestId,
    });
    expect(reply).toMatchObject({ ok: false, error: { code: 'wrong_wait_kind' } });
    expect(frames.length).toBe(before);
  });

  it('resumeFrom replays only the newer frames, to the subscription made before close', async () => {
    const backend = createFakeBackend({ seed: 1, autoplay: false });
    const frames = collect(backend);
    await backend.connect();
    const snapshotSeq = frames[0]!.seq;
    const snapshot = frames[0]!.event;
    if (snapshot.type !== 'snapshot') throw new Error('expected snapshot');
    const waiting = Object.values(snapshot.world.agents).find(
      (a) => a.state === 'awaiting-question',
    );
    if (!waiting?.pendingRequestId) throw new Error('seed has no question');
    await backend.command({
      type: 'agent.answer',
      agentId: waiting.id,
      requestId: waiting.pendingRequestId,
      answers: { any: 'yes' },
    });
    backend.close();

    const before = frames.length;
    await backend.connect({ resumeFrom: snapshotSeq });
    const replayed = frames.slice(before);
    expect(replayed.length).toBeGreaterThan(0);
    expect(replayed.every((f) => f.seq > snapshotSeq)).toBe(true);
    expect(replayed.some((f) => f.event.type === 'snapshot')).toBe(false);
  });

  it('resumeFrom older than the log falls back to a snapshot', async () => {
    const backend = createFakeBackend({ seed: 1, autoplay: false });
    await backend.connect();
    backend.close();
    const replayed = collect(backend);
    await backend.connect({ resumeFrom: -100 });
    expect(replayed[0]?.event.type).toBe('snapshot');
  });

  it('desk.add acks and puts the task on the desk', async () => {
    const backend = createFakeBackend({ seed: 1, autoplay: false });
    const frames = collect(backend);
    await backend.connect();
    const reply = await backend.command({ type: 'desk.add', id: 'task:NORT-3' });
    expect(reply.ok).toBe(true);
    const upsert = frames.find((f) => f.event.type === 'upsert' && f.event.kind === 'desk');
    expect(upsert?.event).toMatchObject({ entity: { id: 'task:NORT-3' } });
  });

  it('desk.remove acks and takes the task off the desk', async () => {
    const backend = createFakeBackend({ seed: 1, autoplay: false });
    const frames = collect(backend);
    await backend.connect();
    const reply = await backend.command({ type: 'desk.remove', id: 'task:NORT-9' });
    expect(reply.ok).toBe(true);
    const removed = frames.find((f) => f.event.type === 'remove' && f.event.kind === 'desk');
    expect(removed?.event).toMatchObject({ id: 'task:NORT-9' });
  });

  it('task.setStatus acks and moves the task', async () => {
    const backend = createFakeBackend({ seed: 1, autoplay: false });
    const frames = collect(backend);
    await backend.connect();
    const reply = await backend.command({
      type: 'task.setStatus',
      taskId: 'NORT-9',
      status: 'done',
    });
    expect(reply.ok).toBe(true);
    const upsert = frames.find(
      (f) => f.event.type === 'upsert' && f.event.kind === 'task' && f.event.entity.id === 'NORT-9',
    );
    expect(upsert?.event).toMatchObject({ entity: { status: 'done' } });
  });

  it('a status move re-decides what is actionable, for the task and its followers', async () => {
    const backend = createFakeBackend({ seed: 1, autoplay: false });
    const frames = collect(backend);
    await backend.connect();
    // NORT-9.1 follows NORT-9, so it is not actionable until NORT-9 is done.
    const snapshot = frames[0]?.event;
    if (snapshot?.type !== 'snapshot') throw new Error('expected snapshot');
    expect(snapshot.world.tasks['NORT-9.1']?.actionable).toBe(false);

    await backend.command({ type: 'task.setStatus', taskId: 'NORT-9', status: 'done' });

    const latest = (id: string) =>
      [...frames]
        .reverse()
        .map((f) => f.event)
        .find((e) => e.type === 'upsert' && e.kind === 'task' && e.entity.id === id);
    expect(latest('NORT-9')).toMatchObject({ entity: { status: 'done', actionable: false } });
    expect(latest('NORT-9.1')).toMatchObject({ entity: { actionable: true } });
  });

  it('task.update acks and writes only the fields it was given', async () => {
    const backend = createFakeBackend({ seed: 1, autoplay: false });
    const frames = collect(backend);
    await backend.connect();
    const reply = await backend.command({
      type: 'task.update',
      taskId: 'NORT-9',
      fields: { title: 'Migrate to Postgres 17' },
    });
    expect(reply.ok).toBe(true);
    const upsert = frames.find(
      (f) => f.event.type === 'upsert' && f.event.kind === 'task' && f.event.entity.id === 'NORT-9',
    );
    expect(upsert?.event).toMatchObject({
      entity: { title: 'Migrate to Postgres 17', branch: 'feat/db-migrate', status: 'in-progress' },
    });
  });

  it('writes only the editable keys, as the server does', async () => {
    const backend = createFakeBackend({ seed: 1, autoplay: false });
    const frames = collect(backend);
    await backend.connect();
    await backend.command({
      type: 'task.update',
      taskId: 'NORT-9',
      // `base` is not on the wire. The server filters it out, so the fake must.
      fields: { branch: 'feat/db-2', base: 'develop' } as TaskEdit,
    });
    const upsert = frames.find(
      (f) => f.event.type === 'upsert' && f.event.kind === 'task' && f.event.entity.id === 'NORT-9',
    );
    expect(upsert?.event).toMatchObject({ entity: { branch: 'feat/db-2', base: '' } });
  });

  it('launching a task that is off the desk puts it on', async () => {
    const backend = createFakeBackend({ seed: 1, autoplay: false });
    const frames = collect(backend);
    await backend.connect();
    const snapshot = frames[0]?.event;
    if (snapshot?.type !== 'snapshot') throw new Error('expected snapshot');
    const task = Object.values(snapshot.world.tasks).find((t) => t.actionable);
    if (!task) throw new Error('seed has no actionable task');
    // The seed desk holds every task still in play, so take this one off it.
    await backend.command({ type: 'desk.remove', id: deskIdForTask(task.id) });
    const before = frames.length;

    const reply = await backend.command({ type: 'agent.launch', taskId: task.id });
    expect(reply.ok).toBe(true);
    const upsert = frames
      .slice(before)
      .find((f) => f.event.type === 'upsert' && f.event.kind === 'desk');
    expect(upsert?.event).toMatchObject({ entity: { id: deskIdForTask(task.id) } });
  });
});
