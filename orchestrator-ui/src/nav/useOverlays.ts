import { useAppStore } from '../store/store';
import type { NewWorkSeed } from '../store/uiSlice';
import type { ProjectId, TaskId } from '../protocol/ids';
import { currentLoc, useRouter } from './router';
import type { ProjectTab } from './location';
import { useGo, useLoc } from './useNav';

/**
 * The task editor, which the location's `edit` opens. A step to the next task replaces the
 * location, and a close goes back: Back never walks through the tasks it showed, or reopens it.
 */
export function useEditor() {
  const { edit } = useLoc();
  const go = useGo();
  const router = useRouter();
  const setEditing = useAppStore((s) => s.setEditorEditing);
  const close = () => {
    setEditing(false);
    go({ edit: null }, { close: true });
  };
  return {
    editingTaskId: edit,
    /** Open a task, in view mode unless `editing` is given. */
    open: (taskId: TaskId, { editing = false }: { editing?: boolean } = {}) => {
      setEditing(editing);
      go({ edit: taskId });
    },
    /** Step to another task. The mode does not change. */
    step: (taskId: TaskId) => go({ edit: taskId }, { replace: true }),
    close,
    /** Close the editor if it is still open on `taskId`, as the location is now. */
    closeIf: (taskId: TaskId) => {
      if (currentLoc(router).edit === taskId) close();
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

/**
 * The **Project dialog**, which the location's `detail` opens on its `dtab` tab. A close goes
 * back. A tab change replaces the entry: Back closes the dialog, not one tab at a time.
 */
export function useProjectDetail() {
  const { projectDetail, projectTab } = useLoc();
  const go = useGo();
  return {
    projectId: projectDetail,
    tab: projectTab,
    open: (projectId: ProjectId, tab: ProjectTab = 'overview') =>
      go({ projectDetail: projectId, projectTab: tab }),
    showTab: (tab: ProjectTab) => go({ projectTab: tab }, { replace: true }),
    close: () => go({ projectDetail: null }, { close: true }),
  };
}

/** The **Welcome dialog**, which the location's `welcome` opens. A close dismisses it. */
export function useWelcome() {
  const { welcome } = useLoc();
  const go = useGo();
  const dismissed = useAppStore((s) => s.ui.welcomeDismissed);
  const dismiss = useAppStore((s) => s.dismissWelcome);
  return {
    asked: welcome,
    dismissed,
    /** Keep it shut for the rest of the page load, with no move. */
    dismiss,
    open: () => go({ welcome: true }),
    close: () => {
      dismiss();
      if (welcome) go({ welcome: false }, { close: true });
    },
  };
}

/** The Add project dialog, which the location's `add` opens. A close goes back. */
export function useAddProjectDialog() {
  const { addProject } = useLoc();
  const go = useGo();
  return {
    adding: addProject,
    /** Open it. From the Welcome dialog, it takes that dialog's place. */
    open: () => go({ addProject: true, welcome: false }),
    close: () => go({ addProject: false }, { close: true }),
    /** Swap it for the Project dialog of the project it added. Back does not reopen it. */
    finish: (projectId: ProjectId) =>
      go(
        { addProject: false, projectDetail: projectId, projectTab: 'overview' },
        { replace: true },
      ),
  };
}
