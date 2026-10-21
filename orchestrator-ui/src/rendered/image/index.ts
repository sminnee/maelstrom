import type { RendererPlugin } from '../plugin';
import { ImageFileView } from './ImageFileView';

export const imagePlugin: RendererPlugin = {
  id: 'image',
  matches: (path) => /\.(png|jpe?g|gif|webp|avif|svg|ico|bmp)$/i.test(path),
  reads: 'bytes',
  View: ImageFileView,
};
