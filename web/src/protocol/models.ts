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

/**
 * A model as the interface says it, from whatever the wire holds.
 *
 * The field is free-form: a task stores the alias it was launched with, and a
 * running agent reports the id Claude resolved that alias to. Both name one
 * model, so both read as the alias — `claude-opus-5` and `opus` alike.
 *
 * An id counts as an alias only when the alias ends it or a `-` follows, so
 * `claude-opusine-9` stays whole rather than reading as `opus`. An id outside
 * `MODELS` passes through whole.
 */
export function modelLabel(model: string): string {
  const match = MODELS.find(
    (m) => model === m || model === `claude-${m}` || model.startsWith(`claude-${m}-`),
  );
  return match ?? model;
}
