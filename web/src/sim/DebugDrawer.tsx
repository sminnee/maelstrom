import { useEffect, useRef } from 'react';
import type { ForcedBeat } from '../protocol/backend';
import { isDebugBackend } from '../protocol/backend';
import { useBackend } from '../store/backendContext';
import { useAppStore } from '../store/store';
import styles from './DebugDrawer.module.css';

/**
 * An upper bound on the ticks a forced beat needs to land: the rest of the
 * current beat (at most two events), then the forced beat's own (at most three).
 */
const TICKS_TO_LAND = 5;

/** FAKE only: a bottom sheet that forces a beat on any live agent. Delete with the fake backend. */
export function DebugDrawer() {
  const backend = useBackend();
  const open = useAppStore((s) => s.ui.drawerOpen);
  const agents = useAppStore((s) => s.world.agents);
  const tasks = useAppStore((s) => s.world.tasks);
  const setDrawerOpen = useAppStore((s) => s.setDrawerOpen);
  const closeButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (open) closeButton.current?.focus();
  }, [open]);
  if (!open || !isDebugBackend(backend)) return null;
  const sim = backend.sim;

  const force = (f: ForcedBeat) => {
    sim.force(f);
    sim.step(TICKS_TO_LAND);
  };

  const live = Object.values(agents).filter((a) => a.state !== 'exited');
  return (
    <div
      className={styles.drawer}
      data-testid="debug-drawer"
      role="region"
      aria-label="Debug drawer"
    >
      <div className={styles.head}>
        <span>Force an event</span>
        <button
          ref={closeButton}
          type="button"
          onClick={() => setDrawerOpen(false)}
          aria-label="Close debug drawer"
        >
          ×
        </button>
      </div>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>agent</th>
            <th>task</th>
            <th>state</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {live.map((a) => (
            <tr key={a.id} data-testid={`drawer-agent-${a.id}`}>
              <td className={styles.mono}>{a.id}</td>
              <td>
                <span className={styles.mono}>{a.taskId}</span> {tasks[a.taskId]?.title}
              </td>
              <td className={styles.mono}>{a.state}</td>
              <td className={styles.actions}>
                {/* A waiting agent plays nothing until it is answered, so a forced beat would sit unseen. */}
                <fieldset className={styles.fieldset} disabled={a.state.startsWith('awaiting-')}>
                  <button type="button" onClick={() => force({ kind: 'ask', agentId: a.id })}>
                    Ask
                  </button>
                  <button
                    type="button"
                    onClick={() => force({ kind: 'permission', agentId: a.id })}
                  >
                    Permission
                  </button>
                  <button type="button" onClick={() => force({ kind: 'plan', agentId: a.id })}>
                    Plan
                  </button>
                  <button type="button" onClick={() => force({ kind: 'finish', agentId: a.id })}>
                    Finish
                  </button>
                </fieldset>
                <button
                  type="button"
                  onClick={() => force({ kind: 'exit', agentId: a.id, exitCode: 1 })}
                >
                  Exit 1
                </button>
              </td>
            </tr>
          ))}
          {live.length === 0 && (
            <tr>
              <td colSpan={4} className={styles.empty}>
                No live agents.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
