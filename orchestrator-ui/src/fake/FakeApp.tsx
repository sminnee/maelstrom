import { useEffect, useState } from 'react';
import { createMemoryRouter } from 'react-router';
import { App, type AppDeps } from '../App';
import type { AppRouter } from '../nav/router';
import { routes } from '../nav/routes';
import { useAppStore } from '../store/store';
import type { UiState } from '../store/uiSlice';
import { fakeDeps } from './fakeDeps';
import { SCENARIOS, type ScenarioName } from './scenarios';
import type { Seed } from './seedWorld';

/** The pattern of a `refuse` parameter. A pattern that does not parse matches as written. */
function routePattern(source: string): RegExp {
  try {
    return new RegExp(source);
  } catch {
    return new RegExp(source.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  }
}

export interface FakeAppProps {
  /** The world to open on. `desk` when left out. */
  scenario?: ScenarioName;
  /** Changes the scenario's seed before the server reads it. */
  amend?: (seed: Seed) => void;
  /** View state to open with: a grouping, a filter. */
  ui?: Partial<UiState>;
  /**
   * Opens the app on a screen. It runs when the first reads have settled: the
   * canvas collapses a node the world does not hold yet.
   */
  open?: () => void;
  /** Hold every reply, so the app stays on its loading state. */
  hold?: boolean;
  /** Refuse each route this pattern matches, so the app shows its error state. */
  refuse?: string;
  /**
   * What reads and moves the location. A memory router on the desk when left out: a story
   * runs on a page whose URL Ladle owns.
   */
  router?: AppRouter;
}

/**
 * The production app on a fake server that holds a scenario.
 *
 * `deps` is the injection point `renderApp` uses, so the fake mode and a story
 * run the production tree and not a stand-in that can drift from it.
 */
export function FakeApp({
  scenario = 'desk',
  amend,
  ui,
  open,
  hold,
  refuse,
  router,
}: FakeAppProps) {
  const [deps] = useState((): AppDeps => {
    // The store is a module singleton. Without the reset, a scenario inherits
    // the view, the filters and the tabs of the one shown before it.
    useAppStore.getState().reset();
    if (ui) useAppStore.setState((s) => ({ ui: { ...s.ui, ...ui } }));
    const seed = SCENARIOS[scenario].build();
    amend?.(seed);
    const { server, deps } = fakeDeps(seed);
    if (hold) server.hold();
    if (refuse)
      server.refuse(routePattern(refuse), { status: 500, code: 'internal', message: 'Refused' });
    return {
      ...deps,
      router: router ?? createMemoryRouter(routes, { initialEntries: ['/desk'] }),
    };
  });
  const { queryClient } = deps;
  useEffect(() => {
    if (!open || !queryClient) return;
    // Held replies never settle, and the loading state of a screen is the reason to hold.
    if (hold) return open();
    const settled = () =>
      queryClient.getQueryCache().getAll().length > 0 && !queryClient.isFetching();
    if (settled()) return open();
    const stop = queryClient.getQueryCache().subscribe(() => {
      if (!settled()) return;
      stop();
      open();
    });
    return stop;
    // Once, for the scenario the page opened on.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <div style={{ height: '100%' }}>
      <App deps={deps} />
    </div>
  );
}
