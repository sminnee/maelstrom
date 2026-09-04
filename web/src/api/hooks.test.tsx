import { describe, expect, it } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { makeAgent, makeQuestionItem, makeTask, worldWith } from '../test/fixtures';
import { createFakeServer } from '../test/fakeServer';
import { useAgent, useAgents } from './agents';
import { ApiProvider } from './ApiProvider';
import { useAttention } from './attention';
import { useDesk } from './desk';
import { useDocument, useDocuments } from './documents';
import { useProjects } from './projects';
import { useTask, useTasks } from './tasks';
import { useWorld } from './useWorld';
import { useWorktrees } from './worktrees';

function harness() {
  const agent = makeAgent({ id: 'ag1', taskId: 'northwind/NORT-7', pendingRequestId: 'req-1' });
  const task = makeTask({ id: 'northwind/NORT-7', content: 'The prose.' });
  const server = createFakeServer({
    world: worldWith({ tasks: [task], agents: [agent] }),
    transcripts: {
      ag1: {
        agentId: 'ag1',
        items: [makeQuestionItem({ requestId: 'req-1' })],
        truncatedBefore: false,
      },
    },
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <ApiProvider api={server.api} queryClient={queryClient}>
      {children}
    </ApiProvider>
  );
  return { server, queryClient, wrapper };
}

describe('the resource hooks', () => {
  it.each([
    ['useProjects', () => useProjects(), '/api/projects'],
    ['useWorktrees', () => useWorktrees(), '/api/worktrees'],
    ['useTasks', () => useTasks(), '/api/tasks'],
    ['useTask', () => useTask('northwind/NORT-7'), '/api/tasks/northwind/NORT-7'],
    ['useAgents', () => useAgents(), '/api/agents'],
    ['useAgent', () => useAgent('ag1'), '/api/agents/ag1'],
    ['useAttention', () => useAttention(), '/api/attention'],
    ['useDesk', () => useDesk(), '/api/desk'],
    ['useDocuments', () => useDocuments(), '/api/documents'],
    ['useDocument', () => useDocument('d1'), '/api/documents/d1'],
  ] as [string, () => { isFetching: boolean }, string][])(
    '%s GETs %s',
    async (_name, hook, path) => {
      const { server, wrapper } = harness();
      const { result } = renderHook(hook, { wrapper });
      await waitFor(() => expect(result.current.isFetching).toBe(false));
      expect(server.requests.map((r) => `${r.method} ${r.path}`)).toEqual([`GET ${path}`]);
    },
  );

  it('a task row has no prose and the detail has it', async () => {
    const { wrapper } = harness();
    const { result } = renderHook(
      () => ({ list: useTasks(), detail: useTask('northwind/NORT-7') }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.detail.data).toBeDefined());
    expect(result.current.list.data?.tasks[0]).not.toHaveProperty('content');
    expect(result.current.detail.data?.content).toBe('The prose.');
  });

  it('an agent detail carries the request it waits on', async () => {
    const { wrapper } = harness();
    const { result } = renderHook(() => useAgent('ag1'), { wrapper });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data?.pendingRequest).toMatchObject({
      type: 'question',
      requestId: 'req-1',
    });
  });

  it('a detail hook with no id fetches nothing', () => {
    const { server, wrapper } = harness();
    renderHook(() => useTask(null), { wrapper });
    expect(server.requests).toEqual([]);
  });
});

describe('useWorld', () => {
  it('is loading until the six required tables have data, then ready with them keyed by id', async () => {
    const { wrapper } = harness();
    const { result } = renderHook(() => useWorld(), { wrapper });
    expect(result.current.status).toBe('loading');
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(Object.keys(result.current.world.tasks)).toEqual(['northwind/NORT-7']);
    expect(result.current.world.agents.ag1?.taskId).toBe('northwind/NORT-7');
  });

  it('keeps the world object when a refetch changes nothing', async () => {
    const { server, queryClient, wrapper } = harness();
    const { result } = renderHook(() => useWorld(), { wrapper });
    await waitFor(() => expect(result.current.status).toBe('ready'));
    const before = result.current.world;
    const requests = server.requests.length;
    await act(async () => {
      await queryClient.refetchQueries();
    });
    // Every list was fetched again, and the same data kept its identity.
    expect(server.requests.length).toBeGreaterThan(requests);
    expect(result.current.world).toBe(before);
  });

  it('reports an error with its message when a required table fails', async () => {
    const { server, wrapper } = harness();
    server.refuse(/GET \/api\/agents$/, { status: 502, code: 'invalid', message: 'bad gateway' });
    const { result } = renderHook(() => useWorld(), { wrapper });
    await waitFor(() => expect(result.current.status).toBe('error'));
    expect(result.current.errors[0]?.message).toBe('bad gateway');
  });
});
