import { changesTab, devEnvTab, documentTab, sessionTab } from '../selectors/tabs';
import { useAppStore } from '../store/store';
import type { Pane } from '../store/uiSlice';

/**
 * Opens the app where the URL says: `view`, `task`, `session`, `document`,
 * `changes`, `devenv`, `split`, `edit` and `new`. The app has no routes of its own, so without
 * this a detail screen is three presses from the scenario's first draw.
 *
 * It calls the store's actions, the ones a press calls, on both layouts: a tab
 * for the panel, and a screen for the narrow stack.
 */
export function openFromParams(params: URLSearchParams) {
  const store = useAppStore.getState();
  const view = params.get('view');
  if (view === 'list' || view === 'worktrees') store.showPane(view satisfies Pane);
  const task = params.get('task');
  if (task) {
    store.expandNode(task);
    store.pushScreen({ kind: 'detail', nodeId: task });
  }
  const session = params.get('session');
  if (session) {
    store.openTab(sessionTab(session));
    store.pushScreen({ kind: 'session', agentId: session });
  }
  const document = params.get('document');
  if (document) {
    store.openTab(documentTab(document));
    store.pushScreen({ kind: 'document', documentId: document });
  }
  const changes = params.get('changes');
  if (changes) {
    store.openTab(changesTab(changes));
    store.pushScreen({ kind: 'changes', worktreeId: changes });
  }
  // `<worktree id>/<service>`. The narrow layout has no screen for it.
  const [worktreeId, service] = params.get('devenv')?.split('/') ?? [];
  if (worktreeId && service) {
    const tab = devEnvTab(worktreeId, service);
    store.openTab(tab);
    // The store has no world to group by, so every open tab counts as the dev
    // env's group. Name a `session` in the same worktree.
    if (params.has('split')) store.toggleSplit(tab.key, () => worktreeId);
  }
  const edit = params.get('edit');
  if (edit) store.setEditingTask(edit);
  if (params.has('new')) store.setNewWorkOpen(true);
}
