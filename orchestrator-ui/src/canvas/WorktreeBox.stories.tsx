import type { Story } from '@ladle/react';
import { useEffect } from 'react';
import { useAppStore } from '../store/store';
import { FakeApp } from '../fake/FakeApp';
import { deskIdForTask } from '../protocol/deskId';
import { makeTask, makeWorktree } from '../fake/fixtures';
import type { Seed } from '../fake/seedWorld';

export default { title: 'Canvas / Worktree boxes' };

/** Open worktrees of northwind that hold nothing, enough to wrap the strip. */
const EMPTY = ['foxtrot', 'golf', 'hotel', 'india', 'juliett', 'kilo', 'lima', 'november'];

/** The seed, with enough worktrees to show the boxes pack. */
function amend(seed: Seed) {
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
}

/** The real app on that world. See `orchestrator-ui/DESIGN.md`, "Seeing a change". */
/**
 * `expand` opens one card once the board has drawn: a node's, or a worktree's.
 * Every story is the real app, so a click on a box label or a node opens its
 * card here as it does on the desk.
 */
function Harness({ expand = {} }: { expand?: { node?: string; worktree?: string } }) {
  // Opened once the board has drawn and fitted, as a click would: a card set
  // before the first draw is placed before the viewport settles, and its pan
  // into view is lost.
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!document.querySelector('[data-testid="worktree-box"]')) return;
      window.clearInterval(timer);
      window.setTimeout(() => {
        const { expandNode, expandWorktree } = useAppStore.getState();
        if (expand.node) expandNode(expand.node, false);
        if (expand.worktree) expandWorktree(expand.worktree, false);
      }, 300);
    }, 50);
    return () => window.clearInterval(timer);
  }, [expand.node, expand.worktree]);
  return (
    <div style={{ height: '100vh' }}>
      <FakeApp amend={amend} />
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

/**
 * What to look at: the Worktree card open on `foxtrot`, an empty box in the
 * strip. The card holds the worktree area and Start free agent. Click another
 * label, or a node, and the card moves there: one card shows at a time.
 */
export const WorktreeCardOnAnEmptyBox: Story = () => (
  <Harness expand={{ worktree: 'northwind-foxtrot' }} />
);

/** What to look at: the Worktree card open on `delta`, a box that holds a node. */
export const WorktreeCardOnAHeldBox: Story = () => (
  <Harness expand={{ worktree: 'northwind-delta' }} />
);

/**
 * What to look at: the node card of NORT-12. The worktree area is its last
 * band, under one hairline, and reads the same as the Worktree card of `delta`.
 */
export const NodeCardWorktreeArea: Story = () => <Harness expand={{ node: 'NORT-12' }} />;
