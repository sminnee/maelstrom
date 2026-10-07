import { useCallback, useMemo, useState } from 'react';
import { useWorld } from '../api/useWorld';
import { DeckList } from '../deck/DeckList';
import { ChangesTab } from '../changes/ChangesTab';
import { DocumentTab } from '../documents/DocumentTab';
import { NodeCardBody } from '../canvas/NodeCardBody';
import { showsProject } from '../selectors/filters';
import { NewWork } from '../newwork/NewWork';
import { useDeck } from '../deck/useDeck';
import { nodeTitle } from '../selectors/graph';
import type { PanelTab } from '../store/uiSlice';
import { TaskEditor } from '../tasklist/TaskEditor';
import { TaskList } from '../tasklist/TaskList';
import { WorktreeTable } from '../worktrees/WorktreeTable';
import { SessionTab } from '../session/SessionTab';
import { useCard } from '../nav/useCard';
import { useBack, useLoc } from '../nav/useNav';
import { SideSheet } from '../ui/SideSheet';
import { ConnectionBanner } from './ConnectionBanner';
import { FilterBar } from './FilterBar';
import { HostBanner } from './HostBanner';
import { type ScreenChrome, ScreenChromeContext } from './screenChromeContext';
import { SheetHead, TopBar } from './TopBar';
import styles from './MobileShell.module.css';

/**
 * The narrow layout: one screen at a time.
 *
 * There is no canvas and no panel. The view is the ground; over it the location's card draws
 * as a node's detail, and over that its panel tab as a session, a document or a worktree's
 * changes. Back goes back a location, which pops the top screen.
 */
export function MobileShell() {
  const { view, panel, edit: editingTaskId, newWork: newWorkOpen } = useLoc();
  const { expandedNodeId } = useCard();
  const onBack = useBack();
  const { status } = useWorld();
  // A worktree's card has no narrow screen, nor has a dev env tab.
  const detail = useMemo(
    (): MobileScreen | undefined =>
      expandedNodeId ? { kind: 'detail', nodeId: expandedNodeId } : undefined,
    [expandedNodeId],
  );
  const top = panel && panel.kind !== 'devenv' ? panel : detail;
  const title = useScreenTitle(top);
  const { chrome, setActionsTarget, setSheetTarget } = useChrome(top);
  const [filtersOpen, setFiltersOpen] = useState(false);
  return (
    <ScreenChromeContext.Provider value={chrome}>
      <div className={styles.shell}>
        <TopBar
          actionsTarget={setActionsTarget}
          filters={{ open: filtersOpen, onOpen: () => setFiltersOpen(true) }}
          back={
            top && chrome
              ? {
                  title: chrome.title ?? title,
                  onBack,
                  sheetOpen: chrome.sheetOpen,
                  onMore: chrome.openSheet,
                }
              : undefined
          }
        />
        <ConnectionBanner hasData={status === 'ready'} />
        <HostBanner />
        <main className={styles.body}>
          {top ? (
            <Screen screen={top} onBack={onBack} />
          ) : view === 'canvas' ? (
            <DeckList />
          ) : view === 'worktrees' ? (
            <WorktreeTable />
          ) : (
            <TaskList />
          )}
        </main>
        {chrome?.sheetOpen && (
          <SideSheet
            label="More"
            onClose={chrome.closeSheet}
            head={<SheetHead onClose={chrome.closeSheet} />}
          >
            <div className={styles.sheetBody} data-sheet ref={setSheetTarget} />
          </SideSheet>
        )}
        {filtersOpen && !top && (
          <SideSheet label="Filters" onClose={() => setFiltersOpen(false)}>
            <FilterBar layout="stack" />
          </SideSheet>
        )}
        {editingTaskId && <TaskEditor key={editingTaskId} taskId={editingTaskId} />}
        {newWorkOpen && <NewWork />}
      </div>
    </ScreenChromeContext.Provider>
  );
}

/** One screen over the narrow layout's view: a node's detail, or a panel tab it has a screen for. */
type MobileScreen = { kind: 'detail'; nodeId: string } | Exclude<PanelTab, { kind: 'devenv' }>;

/**
 * The chrome state of the pushed screen `top`, or `null` on the deck, and the
 * callback refs that set its two DOM targets. They stay out of the context
 * value: a screen portals into a target, it never sets one.
 *
 * The sheet is open for one screen, so a screen change, Back included, closes
 * it with no effect to run.
 */
function useChrome(top: MobileScreen | undefined) {
  const [title, setTitle] = useState<string | null>(null);
  const [actions, setActionsTarget] = useState<HTMLElement | null>(null);
  const [sheet, setSheetTarget] = useState<HTMLElement | null>(null);
  const [sheetFor, setSheetFor] = useState<MobileScreen | null>(null);
  const sheetOpen = top !== undefined && sheetFor === top;
  const openSheet = useCallback(() => setSheetFor(top ?? null), [top]);
  const closeSheet = useCallback(() => setSheetFor(null), []);
  const chrome = useMemo(
    () =>
      top
        ? ({
            title,
            setTitle,
            actions,
            sheet: sheetOpen ? sheet : null,
            sheetOpen,
            openSheet,
            closeSheet,
          } satisfies ScreenChrome)
        : null,
    [top, title, actions, sheet, sheetOpen, openSheet, closeSheet],
  );
  return { chrome, setActionsTarget, setSheetTarget };
}

/** What a pushed screen is, for the screen strip, when the screen names nothing better. */
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
function Screen({ screen, onBack }: { screen: MobileScreen; onBack: () => void }) {
  return (
    <div className={styles.screen}>
      <div className={styles.screenBody}>
        {screen.kind === 'detail' ? (
          <Detail nodeId={screen.nodeId} onDone={onBack} />
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
  const showProject = showsProject(useLoc().filters);
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
