import type { Story } from '@ladle/react';
import { QueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { App } from '../App';
import type { GroupBy } from '../selectors/filters';
import { useAppStore } from '../store/store';
import { createFakeServer } from '../test/fakeServer';
import { makeWorktree } from '../test/fixtures';
import { seedWorld } from '../test/seedWorld';

export default { title: 'Canvas / Worktree boxes' };

/** Open worktrees of northwind that hold nothing, enough to wrap the strip. */
const EMPTY = ['foxtrot', 'golf', 'hotel', 'india', 'juliett', 'kilo', 'lima', 'november'];

/** The real app on the seeded world. See `orchestrator-ui/DESIGN.md`, "Seeing a change". */
function Harness({ groupBy = 'project' }: { groupBy?: GroupBy }) {
  const [deps] = useState(() => {
    // The app's one store, set before the first draw.
    useAppStore.setState((s) => ({ ui: { ...s.ui, groupBy } }));
    const seed = seedWorld();
    for (const nato of EMPTY) {
      const id = `northwind-${nato}`;
      seed.world.worktrees[id] = makeWorktree({ id, nato, branch: `feat/${nato}` });
    }
    const server = createFakeServer({ world: seed.world, transcripts: seed.transcripts });
    return {
      api: server.api,
      eventSourceFactory: server.eventSourceFactory,
      webSocketFactory: server.webSocketFactory,
      streamReconnectMs: 10,
      queryClient: new QueryClient({
        defaultOptions: { queries: { retry: false, staleTime: Infinity } },
      }),
    };
  });
  return (
    <div style={{ height: '100vh' }}>
      <App deps={deps} />
    </div>
  );
}

/**
 * What to look at: in the northwind lane, three boxes each hold the nodes of
 * one worktree, the nodes on a branch with no worktree sit in no box, and the
 * empty worktrees form a strip of small boxes below the last row.
 */
export const ProjectLane: Story = () => <Harness />;

/** The same world grouped by worktree. What to look at: no box draws. */
export const GroupedByWorktree: Story = () => <Harness groupBy="worktree" />;
