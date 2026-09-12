/**
 * Every held-text key, in one place — the way `api/keys.ts` holds every query
 * key.
 *
 * The version sits in the prefix, not in the stored value. A breaking shape
 * change bumps `v1` to `v2`, every `v1` key becomes invisible at once, and no
 * migration code is needed: `useRetained` sweeps the old prefix away on its
 * first mount.
 */
/** The prefix every version shares, so a sweep can tell an old key from a live one. */
export const RETAINED_PREFIX_ALL = 'mael.retained.';

// Derived, not written out again: two literals holding the same segment could
// drift, and a bump that moved only one would leave `isStaleRetainedKey` reading
// every key as live -- silently retiring the sweep.
const PREFIX = `${RETAINED_PREFIX_ALL}v1`;

export const retainedKey = {
  /** The new-work dialog is a singleton, so one constant key. */
  newWork: () => `${PREFIX}.new-work`,
  /**
   * One key per agent. `SessionTab` already keys the agent's attachment bucket
   * by id, so the id is the identity that exists to key by.
   */
  message: (agentId: string) => `${PREFIX}.message.${agentId}`,
};

/** Whether a stored key belongs to a version other than the current one. */
export function isStaleRetainedKey(key: string): boolean {
  return key.startsWith(RETAINED_PREFIX_ALL) && !key.startsWith(`${PREFIX}.`);
}
