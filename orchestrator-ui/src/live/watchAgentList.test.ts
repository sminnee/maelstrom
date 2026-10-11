import { describe, expect, it, vi } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
import { keys } from '../api/keys';
import { makeAgent } from '../fake/fixtures';
import { watchAgentList } from './watchAgentList';

const list = (...ids: string[]) => ({ agents: ids.map((id) => makeAgent({ id })) });

describe('watchAgentList', () => {
  it('ignores the list it found, and fires on a later list that names the agent', async () => {
    const queryClient = new QueryClient();
    queryClient.setQueryData(keys.agents.list(), list('ag1'));
    const onSeen = vi.fn();
    const stop = watchAgentList(queryClient, 'ag1', onSeen);
    expect(onSeen).not.toHaveBeenCalled();

    queryClient.setQueryData(keys.agents.list(), list('ag2'));
    queryClient.setQueryData(keys.agents.detail('ag1'), list('ag1'));
    expect(onSeen).not.toHaveBeenCalled();

    await queryClient.fetchQuery({
      queryKey: keys.agents.list(),
      queryFn: () => list('ag2', 'ag1'),
    });
    expect(onSeen).toHaveBeenCalledTimes(1);

    stop();
    queryClient.setQueryData(keys.agents.list(), list('ag1'));
    expect(onSeen).toHaveBeenCalledTimes(1);
  });

  it('does not fire on a failed fetch of the list', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    queryClient.setQueryData(keys.agents.list(), list('ag1'));
    const onSeen = vi.fn();
    watchAgentList(queryClient, 'ag1', onSeen);
    await queryClient
      .fetchQuery({
        queryKey: keys.agents.list(),
        queryFn: () => Promise.reject(new Error('down')),
        staleTime: 0,
      })
      .catch(() => undefined);
    expect(onSeen).not.toHaveBeenCalled();
  });
});
