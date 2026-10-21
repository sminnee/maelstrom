import type { Story } from '@ladle/react';
import type { Mode } from '../plugin';
import { NO_BYTES } from '../storyProps';
import { TextView } from '../TextView';
import { FeatureView } from './FeatureView';
import { DISPLAY } from './displays';
import { ADDED_FEATURE, NEW_FEATURE, OLD_FEATURE } from './feature.fixture';

export default { title: 'Changes / Rendered view' };

const PATH = 'specs/rotation.feature';

const scenarios =
  (mode: Mode): Story =>
  () => <FeatureView {...NO_BYTES} old={OLD_FEATURE} new={NEW_FEATURE} mode={mode} path={PATH} />;

export const ScenariosChangesOnly = scenarios('changes');
export const ScenariosWholeFile = scenarios('whole');
export const ScenariosFinal = scenarios('final');

/** A new file: one added block, a rule, paragraphs and a wide step table. */
export const ScenariosAddedFile: Story = () => (
  <FeatureView
    {...NO_BYTES}
    old={null}
    new={ADDED_FEATURE}
    mode="whole"
    path="specs/reminders.feature"
  />
);

/** Per scenario: one scenario at a time, with Previous and Next. */
export const ScenariosPerScenario: Story = () => (
  <FeatureView
    {...NO_BYTES}
    old={null}
    new={ADDED_FEATURE}
    mode="whole"
    display={DISPLAY.perScenario}
    path="specs/reminders.feature"
  />
);

/** A file that does not parse falls back to the text view. */
export const ScenariosBroken: Story = () => (
  <FeatureView
    {...NO_BYTES}
    old={OLD_FEATURE}
    new={'Given a step before any feature\n'}
    mode="whole"
    path={PATH}
  />
);

const text =
  (mode: Mode): Story =>
  () => <TextView {...NO_BYTES} old={OLD_FEATURE} new={NEW_FEATURE} mode={mode} path={PATH} />;

export const TextChangesOnly = text('changes');
export const TextWholeFile = text('whole');
export const TextFinal = text('final');
