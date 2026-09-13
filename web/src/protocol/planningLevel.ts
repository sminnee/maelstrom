import type { PermissionMode } from './modes';

/**
 * How much planning the work gets before it is built, highest first.
 *
 * `high` runs the `plan-task` skill and stops at its plan review. `regular`
 * runs the task itself under plan mode, so the agent proposes before it edits.
 * `none` runs it unattended.
 */
export const PLANNING_LEVELS = ['high', 'regular', 'none'] as const;

export type PlanningLevel = (typeof PLANNING_LEVELS)[number];

/** The two wire fields a level stands for. */
export interface PlanningFields {
  command: string;
  mode: PermissionMode;
}

/**
 * The pair each level writes. Mirrors `mode_for_command` in `task.py` for
 * `high` and `none`; `regular` has no equivalent there, because an empty
 * command means `auto` in Python.
 *
 * That is why the level is a reading over `command` and `mode` rather than a
 * field of its own: the notebook already carries both, and a third field would
 * be a second source of truth for the same choice.
 */
const FIELDS: Record<PlanningLevel, PlanningFields> = {
  high: { command: 'plan-task', mode: 'normal' },
  regular: { command: '', mode: 'plan' },
  none: { command: '', mode: 'auto' },
};

/** What choosing a level writes to the task. Total over the levels. */
export function fieldsForLevel(level: PlanningLevel): PlanningFields {
  return FIELDS[level];
}

/**
 * The level a pair reads as, or `null` for a pair no level stands for.
 *
 * Total over every pair, which is what lets the Advanced fields stay free: a
 * task may legally carry a command and a mode this map has no name for — an
 * execute task under `normal`, or any command outside the table — and the
 * dialog says so rather than moving the fields to fit.
 */
/**
 * The level a pair reads as, or `null` for a pair no level stands for.
 *
 * Total over every pair, which is what lets the Advanced fields stay free: the
 * dialog says N/A rather than moving the fields to fit.
 */
export function levelForFields(fields: PlanningFields): PlanningLevel | null {
  return (
    PLANNING_LEVELS.find(
      (level) => FIELDS[level].command === fields.command && FIELDS[level].mode === fields.mode,
    ) ?? null
  );
}
