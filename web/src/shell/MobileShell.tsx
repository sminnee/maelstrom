import { useWorld } from '../api/useWorld';
import { DeckList } from '../deck/DeckList';
import { DocumentTab } from '../documents/DocumentTab';
import { NodeCardBody } from '../canvas/NodeCardBody';
import { NewWork } from '../newwork/NewWork';
import { useDeck } from '../deck/useDeck';
import { nodeTitle } from '../selectors/graph';
import type { MobileScreen } from '../selectors/navStack';
import { TaskEditor } from '../tasklist/TaskEditor';
import { TaskList } from '../tasklist/TaskList';
import { SessionTab } from '../session/SessionTab';
import { useAppStore } from '../store/store';
import { ConnectionBanner } from './ConnectionBanner';
import { TopBar } from './TopBar';
import styles from './MobileShell.module.css';

/**
 * The narrow layout: one screen at a time.
 *
 * There is no canvas and no panel. The deck list is the ground, and a node's
 * detail, a session and a document are pushed over it. Back pops one screen.
 * `mobileStack` holds what is pushed; empty is the deck itself.
 */
export function MobileShell() {
  const view = useAppStore((s) => s.ui.view);
  const stack = useAppStore((s) => s.ui.mobileStack);
  const editingTaskId = useAppStore((s) => s.ui.editingTaskId);
  const newWorkOpen = useAppStore((s) => s.ui.newWorkOpen);
  const { status } = useWorld();
  const top = stack[stack.length - 1];
  return (
    <div className={styles.shell}>
      <TopBar />
      <ConnectionBanner hasData={status === 'ready'} />
      <main className={styles.body}>
        {top ? <Screen screen={top} /> : view === 'canvas' ? <DeckList /> : <TaskList />}
      </main>
      {editingTaskId && <TaskEditor key={editingTaskId} taskId={editingTaskId} />}
      {newWorkOpen && <NewWork />}
    </div>
  );
}

/** One pushed screen, under a bar carrying what it is and the way back. */
function Screen({ screen }: { screen: MobileScreen }) {
  const popScreen = useAppStore((s) => s.popScreen);
  return (
    <div className={styles.screen}>
      <div className={styles.bar}>
        <button type="button" className={styles.back} onClick={popScreen}>
          <span aria-hidden="true">←</span> Back
        </button>
      </div>
      <div className={styles.screenBody}>
        {screen.kind === 'detail' ? (
          <Detail nodeId={screen.nodeId} onDone={popScreen} />
        ) : screen.kind === 'session' ? (
          <SessionTab agentId={screen.agentId} />
        ) : (
          <DocumentTab documentId={screen.documentId} />
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
  // The node has left the desk, or the world no longer holds it.
  if (!node) return <p className={styles.gone}>This work is no longer on the desk.</p>;
  return (
    <div
      className={styles.detail}
      role="dialog"
      aria-label={nodeTitle(node)}
      data-phase={node.phase ?? undefined}
    >
      <NodeCardBody node={node} onDone={onDone} />
    </div>
  );
}
