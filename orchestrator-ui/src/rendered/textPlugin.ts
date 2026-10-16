import type { RendererPlugin } from './plugin';
import { TextView } from './TextView';

export const textPlugin: RendererPlugin = {
  id: 'text',
  label: 'Text',
  matches: () => true,
  View: TextView,
};
