import { useEffect, useRef, useState } from 'react';
import type { GutterHandlers } from '../../ui/DiffRow';
import { rangeOf } from './selection';

interface Drag {
  path: string;
  anchor: number;
  focus: number;
}

/**
 * Select rows of one file by their line numbers: a drag, a click, or Shift and
 * a click. `onSelect` gets the file and its first and last selected row.
 * A touch drag does not extend; see orchestrator-ui.md, "Change comments".
 */
export function useLineSelection(
  onSelect: (path: string, from: number, to: number) => void,
  onCancel: () => void,
) {
  const [drag, setDrag] = useState<Drag | null>(null);
  // Where the last selection began, which Shift+click extends from.
  const anchor = useRef<{ path: string; index: number } | null>(null);

  // The pointer can come up anywhere, so the window hears it.
  useEffect(() => {
    if (!drag) return;
    const up = () => {
      setDrag(null);
      onSelect(drag.path, ...rangeOf(drag.anchor, drag.focus));
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setDrag(null);
    };
    // A touch that turns into a scroll is cancelled and never comes up.
    const cancel = () => setDrag(null);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', cancel);
    window.addEventListener('keydown', key);
    return () => {
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', cancel);
      window.removeEventListener('keydown', key);
    };
  }, [drag, onSelect]);

  /** Whether a Shift+click on a row of `path` has a selection to extend. */
  const extending = (e: { shiftKey: boolean }, path: string) =>
    e.shiftKey && anchor.current?.path === path;

  const handlers = (path: string, index: number): GutterHandlers => ({
    onPointerDown: (e) => {
      if (e.button !== 0 || extending(e, path)) return;
      anchor.current = { path, index };
      setDrag({ path, anchor: index, focus: index });
    },
    onPointerOver: () => {
      if (drag?.path === path && drag.focus !== index) setDrag({ ...drag, focus: index });
    },
    // A plain pointer click is the drag above. Shift extends here and not on
    // pointer-down: the mouse-down that follows a pointer-down moves focus to
    // the button, away from a box opened that early. `detail` is 0 for Enter
    // and Space.
    onClick: (e) => {
      const from = anchor.current;
      if (e.shiftKey && from?.path === path) {
        onSelect(path, ...rangeOf(from.index, index));
      } else if (e.detail === 0) {
        anchor.current = { path, index };
        onSelect(path, index, index);
      }
    },
    onKeyDown: (e) => {
      if (e.key === 'Escape') onCancel();
    },
  });

  /** The rows of `path` the drag in progress covers. */
  const dragged = (path: string): [number, number] | null =>
    drag?.path === path ? rangeOf(drag.anchor, drag.focus) : null;

  return { handlers, dragged, dragging: drag !== null };
}
