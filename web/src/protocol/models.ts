/**
 * The models a form offers, in the order it offers them.
 *
 * The wire field is free-form — `claude --model` takes an alias or a full id,
 * and the notebook stores whatever it is given. This list is the UI's own
 * shortlist over that field, the way `KNOWN_COMMANDS` is over `command`.
 */
export const MODELS = ['opus', 'fable'] as const;

/**
 * The unset model. The launch substitutes the default for it, so a form must be
 * able to say it — `docs/guide/planning.md` asks for it on execute drafts.
 */
export const UNSET_MODEL = '';

/**
 * What the new-work form pre-selects for a free agent, which has no launch to
 * default it. A hand-kept mirror of `task.DEFAULT_MODEL`, like `MODES`.
 */
export const DEFAULT_MODEL = 'opus';
