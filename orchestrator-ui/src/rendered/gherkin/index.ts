import { languageFor } from '../../ui/highlight';
import type { RendererPlugin } from '../plugin';
import { FeatureView } from './FeatureView';
import { DISPLAY } from './displays';

export const gherkinPlugin: RendererPlugin = {
  id: 'gherkin',
  matches: (path) => languageFor(path) === 'gherkin',
  displays: [
    { id: DISPLAY.formatted, label: 'Formatted' },
    { id: DISPLAY.perScenario, label: 'Per scenario' },
  ],
  View: FeatureView,
};
