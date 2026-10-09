import type { TaskRow } from '../api/types';
import type { Comm, EnvLandingState } from '../protocol/entities';

/** The landing steps, lowest first. See CONTEXT.md, "Landing". */
const LANDING_STEPS = ['done', 'merged', 'uat', 'live'] as const;

/**
 * How far a task has landed: -1 before `done`, then the index of its step. A
 * deploy step this list does not know ranks as `done`, the least it can be.
 */
function landingRank(task: TaskRow | undefined): number {
  if (!task?.landing) return -1;
  return Math.max(0, (LANDING_STEPS as readonly string[]).indexOf(task.landing.status));
}

/** The furthest landing step among `tasks`, or `null` when none is done. */
export function highestLanding(tasks: readonly (TaskRow | undefined)[]): string | null {
  let best: TaskRow | undefined;
  for (const task of tasks) if (landingRank(task) > landingRank(best)) best = task;
  return best?.landing?.status ?? null;
}

/** The comms the list shows, newest first. A closed comm shows only when asked. */
export function listComms(comms: readonly Comm[], showClosed: boolean): Comm[] {
  return comms
    .filter((c) => showClosed || !c.closedAt)
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt) || a.id.localeCompare(b.id));
}

/** The distinct non-blank categories, sorted. Mirrors `comms.categories` in Python. */
export function categories(comms: readonly Comm[]): string[] {
  return [...new Set(comms.map((c) => c.category).filter(Boolean))].sort();
}

/** The **Default project** of `category`, or `''`. Mirrors `comms.default_project` in Python. */
export function defaultProject(comms: readonly Comm[], category: string): string {
  if (!category) return '';
  const count = new Map<string, number>();
  const newest = new Map<string, string>();
  for (const c of comms) {
    if (c.category !== category || !c.project) continue;
    count.set(c.project, (count.get(c.project) ?? 0) + 1);
    if (c.createdAt > (newest.get(c.project) ?? '')) newest.set(c.project, c.createdAt);
  }
  let best = '';
  for (const [project, n] of count) {
    const bestN = count.get(best) ?? 0;
    if (n > bestN || (n === bestN && newest.get(project)! > newest.get(best)!)) best = project;
  }
  return best;
}

const ENV_MARKS: Record<EnvLandingState, string> = { landed: '✓', not_yet: '○', unknown: '?' };
const ENV_WORDS: Record<EnvLandingState, string> = {
  landed: 'landed',
  not_yet: 'not yet',
  unknown: 'unknown',
};

export interface LandingPart {
  /** The step's name as the strip shows it. */
  label: string;
  /** The env's mark, or `''` for the status word that leads the strip. */
  mark: string;
  /** The reading in words, for a title. */
  words: string;
}

/**
 * A task's landing as a strip: its step, then each deploy env and its mark,
 * e.g. `merged · UAT ✓ · live ○`. A task not yet done shows its status alone.
 */
export function landingStrip(task: TaskRow): LandingPart[] {
  if (!task.landing) return [{ label: task.status, mark: '', words: task.status }];
  const { status, envs } = task.landing;
  return [
    { label: status, mark: '', words: status },
    ...Object.entries(envs).map(([env, state]) => ({
      label: env === 'uat' ? 'UAT' : env,
      mark: ENV_MARKS[state],
      words: `${env}: ${ENV_WORDS[state]}`,
    })),
  ];
}
