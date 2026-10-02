import type { Story } from '@ladle/react';
import { QueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { App } from '../App';
import { createFakeServer } from '../test/fakeServer';
import { deskIdForTask } from '../protocol/deskId';
import { makeTask, makeWorktree } from '../test/fixtures';
import { seedWorld } from '../test/seedWorld';

export default { title: 'Canvas / Worktree boxes' };

/** Open worktrees of northwind that hold nothing, enough to wrap the strip. */
const EMPTY = ['foxtrot', 'golf', 'hotel', 'india', 'juliett', 'kilo', 'lima', 'november'];

/** The real app on the seeded world. See `orchestrator-ui/DESIGN.md`, "Seeing a change". */
function Harness() {
  const [deps] = useState(() => {
    const seed = seedWorld();
    for (const nato of EMPTY) {
      const id = `northwind-${nato}`;
      seed.world.worktrees[id] = makeWorktree({ id, nato, branch: `feat/${nato}` });
    }
    // A worktree that holds one not-started task, which follows a task of
    // bravo. Its box has no column in common with the box of bravo, so the
    // two sit side by side and the follows edge crosses the two borders.
    const queued = makeTask({
      id: 'NORT-13',
      notebookId: 'NORT-13',
      title: 'Draft invoices',
      branch: 'feat/invoices',
      follows: ['NORT-9.1'],
    });
    seed.world.worktrees['northwind-echo'] = makeWorktree({
      id: 'northwind-echo',
      nato: 'echo',
      branch: queued.branch,
    });
    seed.world.tasks[queued.id] = queued;
    const deskId = deskIdForTask(queued.id);
    seed.world.desk[deskId] = { id: deskId, addedAt: queued.created };
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
 * What to look at: in the northwind lane, four boxes each hold the nodes of
 * one worktree. Boxes with no column in common sit side by side, and their
 * nodes align on one row. The nodes on a branch with no worktree sit in no
 * box, and fill the free cells round the boxes. The empty worktrees form a
 * strip of small boxes below the lowest node and box.
 */
export const ProjectLane: Story = () => <Harness />;
