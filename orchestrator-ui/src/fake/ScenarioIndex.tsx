import { SCENARIOS, screenOf, type ScenarioName } from './scenarios';

/** The page the fake mode opens on with no scenario: each scenario, with a link. */
export function ScenarioIndex() {
  return (
    <main style={{ padding: 'var(--u-2)', maxWidth: '40rem' }}>
      <h1 style={{ fontSize: 'var(--text-lg)' }}>Scenarios</h1>
      <ul style={{ padding: 0, listStyle: 'none' }}>
        {Object.entries(SCENARIOS).map(([name, { about }]) => (
          <li key={name} style={{ marginBottom: 'var(--u-2)' }}>
            <a href={`?scenario=${name}&${screenOf(name as ScenarioName)}`}>{name}</a>
            <div style={{ color: 'var(--fg-muted)' }}>{about}</div>
          </li>
        ))}
      </ul>
      <p style={{ color: 'var(--fg-muted)' }}>
        Add <code>&amp;hold=1</code> for the loading state, or <code>&amp;refuse=/api/tasks</code>{' '}
        for an error state.
      </p>
    </main>
  );
}
