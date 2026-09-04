/**
 * The models a form offers, in the order it offers them.
 *
 * The wire field is free-form — `claude --model` takes an alias or a full id,
 * and the notebook stores whatever it is given. This list is the UI's own
 * shortlist over that field, the way `KNOWN_COMMANDS` is over `command`.
 */
export const MODELS = ['opus', 'fable'] as const;

/**
 * The unset model. The launch picks the default for it, so a task left unset
 * follows that default as it moves. `docs/guide/planning.md` asks for this on
 * execute drafts, so a form must be able to say it.
 */
export const INHERIT_MODEL = '';

/** What a new-work form starts on when nothing has chosen a model. */
export const DEFAULT_MODEL = 'opus';
