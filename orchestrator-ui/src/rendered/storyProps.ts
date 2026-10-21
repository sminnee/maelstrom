import type { RenderedProps } from './plugin';

/** The bytes props of a story that draws text: no side has a URL. */
export const NO_BYTES: Pick<RenderedProps, 'src' | 'sideUrl'> = {
  src: { old: null, new: null },
  sideUrl: () => '',
};
