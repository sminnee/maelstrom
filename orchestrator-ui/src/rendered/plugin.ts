import type { ComponentType } from 'react';

/** The Rendered view's modes. See CONTEXT.md, "Rendered view". */
export type Mode = 'final' | 'whole' | 'changes';

export interface RenderedProps {
  /** `null` when the file is added, binary, or past the size cap. */
  old: string | null;
  /** `null` when the file is deleted, binary, or past the size cap. */
  new: string | null;
  mode: Mode;
  /** One of the plugin's `displays`, by id; unset for a plugin without displays. */
  display?: string;
  /** A side passed the size cap, so a `null` side is unread, not missing. */
  truncated?: boolean;
  path: string;
}

/** A way a plugin draws its file, e.g. its scenarios one at a time. */
export interface DisplayOption {
  id: string;
  label: string;
}

/** One way to draw a file's two versions. See CONTEXT.md, "Renderer plugin". */
export interface RendererPlugin {
  id: string;
  matches: (path: string) => boolean;
  /**
   * The plugin's displays, the first the default. The dialog adds Raw, the
   * text plugin's lines. A plugin without displays draws one way.
   */
  displays?: DisplayOption[];
  View: ComponentType<RenderedProps>;
}
