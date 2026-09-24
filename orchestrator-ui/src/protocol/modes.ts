/**
 * The three permission modes, mirroring `MODES` in `agent_model.py`.
 *
 * One list, in one order: a task launches under a mode, and a running agent
 * cycles between them. A hand-kept mirror, like the rest of `entities.ts`.
 * The wire carries maelstrom's own words only.
 */
export const MODES = ['plan', 'auto', 'normal'] as const;

export type PermissionMode = (typeof MODES)[number];

/** The mode after `mode` in the cycle. An unknown mode starts it over. */
export function nextMode(mode: string): PermissionMode {
  const at = (MODES as readonly string[]).indexOf(mode);
  return at === -1 ? MODES[0] : MODES[(at + 1) % MODES.length]!;
}
