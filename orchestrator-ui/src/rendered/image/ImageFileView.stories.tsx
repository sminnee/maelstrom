import type { Story } from '@ladle/react';
import type { Mode } from '../plugin';
import { ImageFileView } from './ImageFileView';

export default { title: 'Changes / Rendered view' };

/** A badge drawn as an SVG data URL, so a story needs no server. */
const badge = (fill: string, label: string, width: number) =>
  `data:image/svg+xml,${encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="120"><rect x="10" y="10" width="${width - 20}" height="100" rx="16" fill="${fill}"/><text x="${width / 2}" y="70" font-family="sans-serif" font-size="28" text-anchor="middle" fill="white">${label}</text></svg>`,
  )}`;

const OLD = badge('#c2410c', 'v1', 240);
const NEW = badge('#15803d', 'v2', 320);
const PATH = 'docs/badge.svg';
const sideUrl = () => '';

const image =
  (mode: Mode): Story =>
  () => (
    <ImageFileView
      old={null}
      new={null}
      src={{ old: OLD, new: NEW }}
      sideUrl={sideUrl}
      mode={mode}
      path={PATH}
    />
  );

export const ImageChangesOnly = image('changes');
export const ImageFinal = image('final');

/** A new image: its one side, tinted added. */
export const ImageAddedFile: Story = () => (
  <ImageFileView
    old={null}
    new={null}
    src={{ old: null, new: NEW }}
    sideUrl={sideUrl}
    mode="whole"
    path={PATH}
  />
);

/** A side that does not load says so. */
export const ImageBroken: Story = () => (
  <ImageFileView
    old={null}
    new={null}
    src={{ old: OLD, new: 'data:image/png;base64,broken' }}
    sideUrl={sideUrl}
    mode="whole"
    path="docs/shot.png"
  />
);
