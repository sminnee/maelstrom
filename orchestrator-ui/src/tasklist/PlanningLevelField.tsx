import { useId } from 'react';
import type { PermissionMode } from '../protocol/modes';
import type { PlanningLevel } from '../protocol/planningLevel';
import { PLANNING_LEVELS, fieldsForLevel, levelForFields } from '../protocol/planningLevel';
import styles from '../ui/Dialog.module.css';

/** What each level is called on screen. */
const LABELS: Record<PlanningLevel, string> = {
  high: 'High',
  regular: 'Regular',
  none: 'None',
};

/**
 * How much planning the work gets, as a radio group over `command` and `mode`.
 *
 * The radio and the two Advanced fields are one value read two ways, never two
 * values kept in step: choosing a level writes both fields, and editing either
 * field re-derives the level. A pair no level stands for shows an N/A radio,
 * which appears only while it is selected — there is nothing to choose there,
 * it only says where the fields have got to.
 */
export function PlanningLevelField({
  command,
  mode,
  onChange,
  readOnly,
}: {
  command: string;
  mode: PermissionMode;
  onChange: (fields: { command: string; mode: PermissionMode }) => void;
  readOnly?: boolean;
}) {
  // Document-global, so nothing else on the page may share it.
  const name = useId();
  const level = levelForFields({ command, mode });

  return (
    <fieldset className={styles.kinds} disabled={readOnly}>
      <legend>Planning</legend>
      {PLANNING_LEVELS.map((value) => (
        <label key={value} className={styles.kind}>
          <input
            type="radio"
            name={name}
            value={value}
            checked={level === value}
            onChange={() => onChange(fieldsForLevel(value))}
          />
          <span>{LABELS[value]}</span>
        </label>
      ))}
      {/* A reading of Advanced, not a level to choose. */}
      {level === null && (
        <label className={styles.kind}>
          <input type="radio" name={name} value="n/a" checked readOnly />
          <span>N/A</span>
        </label>
      )}
    </fieldset>
  );
}
