import { useId } from 'react';
import { useAnchorName } from './useAnchorName';
import { usePopoverMenu } from './usePopoverMenu';
import menuStyles from './popoverMenu.module.css';
import styles from './MultiSelect.module.css';

/**
 * A pick of any number of options, as one control.
 *
 * Collapsed, it reads as a select box: a field with a caret at its right edge,
 * showing the picked labels in the options' order. A long list is cut short
 * with an ellipsis. Its menu holds one check item for each option. A click
 * toggles the item and keeps the menu open, so several picks cost one open.
 *
 * `label` shows before the field and names it. `className` styles the pair,
 * so a caller lays the label out as its other fields.
 */
export function MultiSelect<V extends string>({
  label,
  options,
  value,
  onChange,
  emptyLabel = 'none',
  className,
}: {
  label: string;
  options: readonly { value: V; label: string }[];
  value: readonly V[];
  onChange: (value: V[]) => void;
  /** What an empty pick means to the caller. A filter that reads empty as no filter says `all`. */
  emptyLabel?: string;
  className?: string;
}) {
  const { anchorStyle } = useAnchorName();
  const menu = usePopoverMenu();
  const labelId = useId();
  const valueId = useId();
  const picked = options.filter((o) => value.includes(o.value)).map((o) => o.label);
  const shown = picked.length === 0 ? emptyLabel : picked.join(', ');

  const toggle = (v: V) =>
    onChange(value.includes(v) ? value.filter((item) => item !== v) : [...value, v]);

  return (
    <span className={className}>
      <span id={labelId}>{label}</span>
      <button
        {...menu.triggerProps}
        type="button"
        className={styles.trigger}
        style={anchorStyle}
        aria-labelledby={`${labelId} ${valueId}`}
        title={shown}
      >
        <span id={valueId} className={styles.value}>
          {shown}
        </span>
        <svg className={styles.caret} viewBox="0 0 10 6" aria-hidden="true">
          <path d="M1 1l4 4 4-4" />
        </svg>
      </button>
      {/* Named by the label alone: a name does not chain through the
          trigger's own `aria-labelledby`. */}
      <div
        {...menu.menuProps}
        aria-labelledby={labelId}
        className={menuStyles.menu}
        style={anchorStyle}
      >
        {options.map((option) => {
          const checked = value.includes(option.value);
          return (
            <button
              key={option.value}
              type="button"
              role="menuitemcheckbox"
              aria-checked={checked}
              className={styles.item}
              tabIndex={-1}
              onClick={() => toggle(option.value)}
            >
              <span className={styles.box} aria-hidden="true">
                {checked ? '✓' : ''}
              </span>
              {option.label}
            </button>
          );
        })}
      </div>
    </span>
  );
}
