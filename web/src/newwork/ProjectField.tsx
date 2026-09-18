import { useId } from 'react';
import styles from '../ui/Dialog.module.css';

/**
 * Which project the work belongs to: a radio per project on the canvas, and
 * `Other` for the rest.
 *
 * The radios follow the filter bar, because they read the canvas view — so the
 * common case, working on what is already on screen, is no click at all. The
 * long tail stays reachable behind one radio rather than costing everyone a
 * dropdown.
 */
export function ProjectField({
  names,
  inView,
  project,
  setProject,
}: {
  /** Every project the world has, for the `Other` select. */
  names: string[];
  /** The projects the canvas is drawing. Empty falls back to `names`. */
  inView: string[];
  project: string;
  setProject: (name: string) => void;
}) {
  // Document-global, so nothing else on the page may share them.
  const name = useId();
  const otherId = useId();
  // Nothing drawn means no view to read a project off, so every project is
  // offered rather than an empty fieldset.
  const offered = inView.length > 0 ? inView : names;
  const rest = names.filter((n) => !offered.includes(n));
  // `Other` is showing when the chosen project is not one of the radios. The
  // select then carries the choice, so the radio needs no state of its own.
  const other = project !== '' && !offered.includes(project);

  return (
    <>
      <fieldset className={styles.kinds}>
        <legend>Project</legend>
        {offered.map((value) => (
          <label key={value} className={styles.kind}>
            <input
              type="radio"
              name={name}
              value={value}
              checked={!other && project === value}
              onChange={() => setProject(value)}
            />
            <span>{value}</span>
          </label>
        ))}
        {rest.length > 0 && (
          <label className={styles.kind}>
            <input
              type="radio"
              name={name}
              checked={other}
              // The first of the rest, so choosing `Other` names a project at
              // once rather than leaving the form on none.
              onChange={() => setProject(rest[0]!)}
            />
            <span>Other</span>
          </label>
        )}
      </fieldset>

      {other && (
        <div className={styles.field}>
          <label htmlFor={otherId}>Other project</label>
          <select id={otherId} value={project} onChange={(e) => setProject(e.target.value)}>
            {rest.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </div>
      )}
    </>
  );
}
