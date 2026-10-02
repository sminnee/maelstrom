import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource/inter-tight/latin-600.css';
import '../styles/tokens.css';
import '../styles/base.css';
import { trackVisualViewport } from '../layout/visualViewport';
import { openFromParams } from './deepLink';
import { FakeApp } from './FakeApp';
import { ScenarioIndex } from './ScenarioIndex';
import { isScenarioName } from './scenarios';

/** The fake mode's entry. DESIGN.md, "Seeing a change", lists the URL parameters. */
trackVisualViewport(document.documentElement, window.visualViewport);

const params = new URLSearchParams(window.location.search);
const scenario = params.get('scenario');

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {isScenarioName(scenario) ? (
      <FakeApp
        scenario={scenario}
        open={() => openFromParams(params)}
        hold={params.has('hold')}
        refuse={params.get('refuse') ?? undefined}
      />
    ) : (
      <ScenarioIndex />
    )}
  </StrictMode>,
);
