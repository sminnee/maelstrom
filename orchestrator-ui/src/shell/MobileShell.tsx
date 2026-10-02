import { useWorld } from '../api/useWorld';
import { DeckList } from '../deck/DeckList';
import { ChangesTab } from '../changes/ChangesTab';
import { DocumentTab } from '../documents/DocumentTab';
import { NodeCardBody } from '../canvas/NodeCardBody';
import { showsProject } from '../selectors/filters';
import { NewWork } from '../newwork/NewWork';
import { useDeck } from '../deck/useDeck';
import { nodeTitle } from '../selectors/graph';
import type { MobileScreen } from '../selectors/navStack';
import { mainView } from '../selectors/slots';
import { TaskEditor } from '../tasklist/TaskEditor';
import { TaskList } from '../tasklist/TaskList';
import { WorktreeTable } from '../worktrees/WorktreeTable';
import { SessionTab } from '../session/SessionTab';
import { useAppStore } from '../store/store';
import { ConnectionBanner } from './ConnectionBanner';
import { HostBanner } from './HostBanner';
import { TopBar } from './TopBar';
import styles from './MobileShell.module.css';

/**
 * The narrow layout: one screen at a time.
 *
 * There is no canvas and no panel. The deck list is the ground, and a node's
 * detail, a session, a document and a worktree's changes are pushed over it. Back pops one screen.
 * `mobileStack` holds what is pushed; empty is the deck itself.
 */
export function MobileShell() {
  // The panel has no place here, so the view is the most recent main view.
  const view = useAppStore((s) => mainView(s.ui));
  const stack = useAppStore((s) => s.ui.mobileStack);
  const editingTaskId = useAppStore((s) => s.ui.editingTaskId);
  const newWorkOpen = useAppStore((s) => s.ui.newWorkOpen);
  const { status } = useWorld();
  const popScreen = useAppStore((s) => s.popScreen);
  const top = stack[stack.length - 1];
  const title = useScreenTitle(top);
  return (
    <div className={styles.shell}>
      <TopBar back={top ? { title, onBack: popScreen } : undefined} />
      <ConnectionBanner hasData={status === 'ready'} />
      <HostBanner />
      <main className={styles.body}>
        {top ? (
          <Screen screen={top} />
        ) : view === 'canvas' ? (
          <DeckList />
        ) : view === 'worktrees' ? (
          <WorktreeTable />
        ) : (
          <TaskList />
        )}
      </main>
      {editingTaskId && <TaskEditor key={editingTaskId} taskId={editingTaskId} />}
      {newWorkOpen && <NewWork />}
    </div>
  );
}

/** What a pushed screen is, for the top bar's second row. */
function useScreenTitle(screen: MobileScreen | undefined): string {
  const { world } = useWorld();
  const node = useDeck().byId.get(screen?.kind === 'detail' ? screen.nodeId : '');
  switch (screen?.kind) {
    case 'detail':
      return node ? nodeTitle(node) : '';
    case 'session':
      return 'Session';
    case 'changes':
      return 'Changes';
    case 'document':
      return world.documents[screen.documentId]?.title ?? 'Document';
    default:
      return '';
  }
}

/** One pushed screen. The top bar carries what it is and the way back. */
function Screen({ screen }: { screen: MobileScreen }) {
  const popScreen = useAppStore((s) => s.popScreen);
  return (
    <div className={styles.screen}>
      <div className={styles.screenBody}>
        {screen.kind === 'detail' ? (
          <Detail nodeId={screen.nodeId} onDone={popScreen} />
        ) : screen.kind === 'session' ? (
          <SessionTab key={screen.agentId} agentId={screen.agentId} />
        ) : screen.kind === 'changes' ? (
          <ChangesTab key={screen.worktreeId} worktreeId={screen.worktreeId} />
        ) : (
          <DocumentTab key={screen.documentId} documentId={screen.documentId} />
        )}
      </div>
    </div>
  );
}

/**
 * A node's detail, full-screen. It renders the same body the canvas card does,
 * so the two surfaces cannot drift on what a node says.
 *
 * The node is read from the deck rather than passed in, because a change
 * notice must reach it: the screen holds an id, and the world moves under it.
 */
function Detail({ nodeId, onDone }: { nodeId: string; onDone: () => void }) {
  const node = useDeck().byId.get(nodeId);
  const showProject = useAppStore((s) => showsProject(s.ui.filters));
  // The node has left the desk, or the world no longer holds it.
  if (!node) return <p className={styles.gone}>This work is no longer on the desk.</p>;
  return (
    <div
      className={styles.detail}
      role="dialog"
      aria-label={nodeTitle(node)}
      data-phase={node.phase ?? undefined}
    >
      <NodeCardBody node={node} showProject={showProject} onDone={onDone} />
    </div>
  );
}
