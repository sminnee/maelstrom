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
  /** The old side's path, when the file was renamed. */
  oldPath?: string | null;
  /**
   * The URL of each side's raw bytes, `null` for a side the file does not
   * have. A `bytes` plugin draws from these; a text plugin resolves a
   * relative ref in the file with `sideUrl`.
   */
  src: { old: string | null; new: string | null };
  /** The raw-bytes URL of another file of the rev, on one side. */
  sideUrl: (path: string, side: Side) => string;
}

export type Side = 'old' | 'new';

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
  /**
   * What the plugin reads: the two sides as text (the default), or only
   * their raw-bytes URLs, `src`. A `bytes` plugin draws a binary file.
   */
  reads?: 'text' | 'bytes';
  View: ComponentType<RenderedProps>;
}
