import { useCallback, useEffect, useId, useRef } from 'react';
import type { TaskEdit } from '../api/types';
import type { PermissionMode } from '../protocol/modes';
import { MODES } from '../protocol/modes';
import { INHERIT_MODEL, MODELS } from '../protocol/models';
import { KNOWN_COMMANDS } from '../protocol/phase';
import styles from '../ui/Dialog.module.css';

/** From `task.PRIORITIES`, highest first. */
const PRIORITIES = ['critical', 'high', 'medium', 'low'];

/** Every editable field of a task, as a form holds them. */
export type TaskDraft = Required<TaskEdit>;

/**
 * A task's fields: Title, Content and Branch, with the rest folded into
 * Advanced. Both surfaces that write a task render this same component — the
 * editor over an existing task, the new-work form over an inferred one — so
 * the two cannot drift apart on what a task's fields are or how they read.
 */
export function TaskFields({
  draft,
  onChange,
}: {
  draft: TaskDraft;
  onChange: (patch: Partial<TaskDraft>) => void;
}) {
  // The content field shows the whole task body: it grows to fit, and the
  // dialog scrolls.
  // Document-global, so two field sets on one page must not share it.
  const commands = useId();
  const content = useRef<HTMLTextAreaElement>(null);
  const grow = useCallback((el: HTMLTextAreaElement | null) => {
    content.current = el;
    fitToText(el);
  }, []);
  useEffect(() => fitToText(content.current), [draft.content]);

  return (
    <>
      <label className={styles.field}>
        <span>Title</span>
        <input value={draft.title} onChange={(e) => onChange({ title: e.target.value })} />
      </label>
      <label className={styles.field}>
        <span>Content</span>
        <textarea
          ref={grow}
          rows={1}
          value={draft.content}
          onChange={(e) => onChange({ content: e.target.value })}
        />
      </label>
      <label className={styles.field}>
        <span>Branch</span>
        <input value={draft.branch} onChange={(e) => onChange({ branch: e.target.value })} />
      </label>

      <details className={styles.advanced}>
        <summary>Advanced</summary>
        <label className={styles.field}>
          <span>Command</span>
          {/* Free-form in the notebook, so this offers the known ones and
              keeps anything else typed. Empty runs the task itself. */}
          <input
            list={commands}
            value={draft.command}
            onChange={(e) => onChange({ command: e.target.value })}
          />
          <datalist id={commands}>
            <option value="" />
            {KNOWN_COMMANDS.map((c) => (
              <option key={c} value={c} />
            ))}
          </datalist>
        </label>
        <label className={styles.field}>
          <span>Mode</span>
          <ModeSelect mode={draft.mode} onChange={(mode) => onChange({ mode })} />
        </label>
        <label className={styles.field}>
          <span>Priority</span>
          <select value={draft.priority} onChange={(e) => onChange({ priority: e.target.value })}>
            {PRIORITIES.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        <label className={styles.field}>
          <span>Model</span>
          <ModelSelect model={draft.model} onChange={(model) => onChange({ model })} />
        </label>
      </details>
    </>
  );
}

/** The permission mode a session launches under. */
export function ModeSelect({
  mode,
  onChange,
}: {
  mode: PermissionMode;
  onChange: (mode: PermissionMode) => void;
}) {
  return (
    <select value={mode} onChange={(e) => onChange(e.target.value as PermissionMode)}>
      {MODES.map((m) => (
        <option key={m} value={m}>
          {m}
        </option>
      ))}
    </select>
  );
}

/** The model a session runs under. A stored model outside `MODELS` is offered too. */
export function ModelSelect({
  model,
  onChange,
}: {
  model: string;
  onChange: (model: string) => void;
}) {
  const offered: readonly string[] = [INHERIT_MODEL, ...MODELS];
  return (
    <select value={model} onChange={(e) => onChange(e.target.value)}>
      {(offered.includes(model) ? offered : [...offered, model]).map((m) => (
        <option key={m} value={m}>
          {m === INHERIT_MODEL ? 'not set' : m}
        </option>
      ))}
    </select>
  );
}

/** Set a textarea's height to the height of its text. */
function fitToText(el: HTMLTextAreaElement | null) {
  if (!el) return;
  // Collapse first, or the height only ever grows: `scrollHeight` includes
  // whatever height is already set.
  el.style.height = 'auto';
  el.style.height = `${el.scrollHeight}px`;
}
