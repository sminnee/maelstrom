import { useAppStore } from '../store/store';
import type { NewWorkSeed } from '../store/uiSlice';
import type { TaskId } from '../protocol/ids';
import { useGo, useLoc } from './useNav';

/**
 * The task editor, which the location's `edit` opens. A step to the next task replaces the
 * location: Back closes the editor rather than walking back through each task it showed.
 */
export function useEditor() {
  const { edit } = useLoc();
  const go = useGo();
  return {
    editingTaskId: edit,
    open: (taskId: TaskId) => go({ edit: taskId }),
    step: (taskId: TaskId) => go({ edit: taskId }, { replace: true }),
    close: () => go({ edit: null }),
  };
}

/**
 * The new-work form, which the location's `new` opens. The seed a surface opens it on stays in
 * the store: a URL carries where the user is, not a draft.
 */
export function useNewWork() {
  const { newWork } = useLoc();
  const go = useGo();
  const setSeed = useAppStore((s) => s.setNewWorkSeed);
  return {
    newWorkOpen: newWork,
    /** Open the form, on `seed` if given. A surface that opens it also closes its card. */
    open: (seed?: NewWorkSeed) => {
      setSeed(seed ?? null);
      go({ newWork: true, ...(seed ? { card: null } : {}) });
    },
    close: () => {
      setSeed(null);
      go({ newWork: false });
    },
  };
}
