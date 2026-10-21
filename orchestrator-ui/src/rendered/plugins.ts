import { gherkinPlugin } from './gherkin';
import { markdownPlugin } from './markdown';
import type { RendererPlugin } from './plugin';
import { textPlugin } from './textPlugin';

/** The specific plugins, first match wins. A new file type is one new entry. */
const PLUGINS: RendererPlugin[] = [gherkinPlugin, markdownPlugin];

/** The plugin that draws `path`: a specific one, else plain text. */
export function rendererFor(path: string): RendererPlugin {
  return PLUGINS.find((p) => p.matches(path)) ?? textPlugin;
}
