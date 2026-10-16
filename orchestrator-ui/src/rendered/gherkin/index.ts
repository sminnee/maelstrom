import { languageFor } from '../../ui/highlight';
import type { RendererPlugin } from '../plugin';
import { FeatureView } from './FeatureView';

export const gherkinPlugin: RendererPlugin = {
  id: 'gherkin',
  label: 'Scenarios',
  matches: (path) => languageFor(path) === 'gherkin',
  View: FeatureView,
};
