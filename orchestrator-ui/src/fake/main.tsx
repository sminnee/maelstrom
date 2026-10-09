import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter } from 'react-router';
import '@fontsource/inter-tight/latin-600.css';
import '../styles/tokens.css';
import '../styles/base.css';
import '../styles/text.css';
import { trackVisualViewport } from '../layout/visualViewport';
import { routes } from '../nav/routes';
import { FakeApp } from './FakeApp';
import { ScenarioIndex } from './ScenarioIndex';
import { isScenarioName, SCENARIOS, type Scenario } from './scenarios';

/** The fake mode's entry. `/scenario/<name>` is the app's base: see DESIGN.md, "Seeing a change". */
trackVisualViewport(document.documentElement, window.visualViewport);

const params = new URLSearchParams(window.location.search);
const scenario = window.location.pathname.match(/^\/scenario\/([^/]+)/)?.[1] ?? null;

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {isScenarioName(scenario) ? (
      <FakeApp
        scenario={scenario}
        ui={(SCENARIOS[scenario] as Scenario).ui}
        router={createBrowserRouter(routes, { basename: `/scenario/${scenario}` })}
        keepTabs
        hold={params.has('hold')}
        refuse={params.get('refuse') ?? undefined}
      />
    ) : (
      <ScenarioIndex />
    )}
  </StrictMode>,
);
