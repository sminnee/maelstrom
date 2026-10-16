import type { ComponentType } from 'react';

/** The Rendered view's modes. See CONTEXT.md, "Rendered view". */
export type Mode = 'final' | 'whole' | 'changes';

export interface RenderedProps {
  /** `null` when the file is added, binary, or past the size cap. */
  old: string | null;
  /** `null` when the file is deleted, binary, or past the size cap. */
  new: string | null;
  mode: Mode;
  path: string;
}

/** One way to draw a file's two versions. See CONTEXT.md, "Renderer plugin". */
export interface RendererPlugin {
  id: string;
  /** Named in the dialog's head, e.g. "Scenarios". */
  label: string;
  matches: (path: string) => boolean;
  View: ComponentType<RenderedProps>;
}
