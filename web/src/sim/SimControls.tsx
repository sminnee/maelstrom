import { useEffect, useState } from 'react';
import { isDebugBackend } from '../protocol/backend';
import { useBackend } from '../store/backendContext';
import { useAppStore } from '../store/store';
import styles from './SimControls.module.css';

const SPEEDS = [0.5, 1, 2, 4, 8];

/** The FAKE chip: play, pause, speed and the debug drawer. Only with a DebugBackend. */
export function SimControls() {
  const backend = useBackend();
  const setDrawerOpen = useAppStore((s) => s.setDrawerOpen);
  const drawerOpen = useAppStore((s) => s.ui.drawerOpen);
  const [, rerender] = useState(0);
  const sim = isDebugBackend(backend) ? backend.sim : null;

  useEffect(() => {
    if (!sim) return;
    return sim.subscribe(() => rerender((n) => n + 1));
  }, [sim]);

  if (!sim) return null;
  const { playing, speed, tick } = sim.state;
  return (
    <div className={styles.chip} data-testid="sim-controls">
      <span className={styles.label}>FAKE</span>
      <button
        type="button"
        aria-label={playing ? 'Pause simulation' : 'Play simulation'}
        onClick={() => {
          if (playing) sim.pause();
          else sim.play();
        }}
      >
        {playing ? '⏸' : '▶'}
      </button>
      <button type="button" aria-label="Step simulation" onClick={() => sim.step()}>
        ⏭
      </button>
      <select
        aria-label="Simulation speed"
        value={speed}
        onChange={(e) => sim.setSpeed(Number(e.target.value))}
      >
        {SPEEDS.map((s) => (
          <option key={s} value={s}>
            {s}x
          </option>
        ))}
      </select>
      <span className={styles.tick}>t={tick}</span>
      <button
        type="button"
        aria-label="Toggle debug drawer"
        aria-pressed={drawerOpen}
        onClick={() => setDrawerOpen(!drawerOpen)}
      >
        ⚙
      </button>
    </div>
  );
}
