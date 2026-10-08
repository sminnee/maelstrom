import { useAppStore } from '../store/store';
import type { NewWorkSeed } from '../store/uiSlice';
import type { TaskId } from '../protocol/ids';
import { currentLoc, useRouter } from './router';
import { useGo, useLoc } from './useNav';

/**
 * The task editor, which the location's `edit` opens. A step to the next task replaces the
 * location, and a close goes back: Back never walks through the tasks it showed, or reopens it.
 */
export function useEditor() {
  const { edit } = useLoc();
  const go = useGo();
  const router = useRouter();
  return {
    editingTaskId: edit,
    open: (taskId: TaskId) => go({ edit: taskId }),
    step: (taskId: TaskId) => go({ edit: taskId }, { replace: true }),
    close: () => go({ edit: null }, { close: true }),
    /** Close the editor if it is still open on `taskId`, as the location is now. */
    closeIf: (taskId: TaskId) => {
      if (currentLoc(router).edit === taskId) go({ edit: null }, { close: true });
    },
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
      go({ newWork: false }, { close: true });
    },
  };
}
