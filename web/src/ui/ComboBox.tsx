import { useEffect, useId, useMemo, useRef, useState } from 'react';
import styles from './ComboBox.module.css';

/** One row of the offer. `label` names the value; the field shows the value alone. */
export interface ComboOption {
  value: string;
  label?: string;
}

/**
 * A text field that offers a list, and keeps anything else typed.
 *
 * A row shows `value` and `label`; the field submits the value alone, so an
 * id-shaped option is choosable by its words. `<datalist>` cannot do this — an
 * option's `value` is both what shows and what submits.
 *
 * The offer narrows to what is typed and closes when nothing matches, so a
 * free-text value is never blocked by an empty box. Use a `<select>` instead
 * where free text is not a legal answer.
 */
export function ComboBox({
  value,
  options,
  onChange,
  id,
  placeholder,
}: {
  value: string;
  options: readonly ComboOption[];
  onChange: (value: string) => void;
  id?: string;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  /** The row the keyboard is on, or -1 for none. Reset whenever the offer moves. */
  const [active, setActive] = useState(-1);
  const listId = useId();
  const rowId = useId();
  const box = useRef<HTMLDivElement>(null);

  const offered = useMemo(() => {
    const needle = value.trim().toLowerCase();
    if (!needle) return options;
    return options.filter(
      (o) =>
        o.value.toLowerCase().includes(needle) || (o.label ?? '').toLowerCase().includes(needle),
    );
  }, [options, value]);

  // An empty offer shows no box: the value is free text, not a dead end.
  const showing = open && offered.length > 0;
  const activeId = showing && active >= 0 ? `${rowId}-${active}` : undefined;

  // A click outside is a dismissal. `mousedown`, not `click`, so the box is
  // gone before the thing under the pointer takes the press.
  useEffect(() => {
    if (!showing) return;
    const onDown = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [showing]);

  const choose = (option: ComboOption) => {
    onChange(option.value);
    setOpen(false);
    setActive(-1);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      if (!open) return;
      // A Dialog listens for Escape on the document to close itself, so the key
      // must stop here: one press dismisses the offer, not the dialog too.
      e.stopPropagation();
      e.preventDefault();
      setOpen(false);
      setActive(-1);
      return;
    }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!offered.length) return;
      setOpen(true);
      // Wraps. Unwalked (-1), Down opens on the first row and Up on the last.
      const step = e.key === 'ArrowDown' ? 1 : -1;
      setActive((was) =>
        was < 0
          ? step === 1
            ? 0
            : offered.length - 1
          : (was + step + offered.length) % offered.length,
      );
      return;
    }
    if (e.key === 'Enter' && showing && active >= 0) {
      const row = offered[active];
      if (!row) return;
      e.preventDefault();
      choose(row);
    }
  };

  return (
    <div
      className={styles.box}
      ref={box}
      // A blur inside the box is a move between its own parts; one that lands
      // outside has left the control.
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOpen(false);
      }}
    >
      <input
        id={id}
        role="combobox"
        aria-expanded={showing}
        aria-controls={showing ? listId : undefined}
        aria-activedescendant={activeId}
        aria-autocomplete="list"
        autoComplete="off"
        placeholder={placeholder}
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
          setOpen(true);
          setActive(-1);
        }}
        // Focus alone opens it. A click on a row keeps the focus here and so
        // reaches this input too -- an `onClick` that opens would undo the
        // choice the row just made.
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
      />
      {showing && (
        <ul className={styles.list} id={listId} role="listbox">
          {offered.map((option, i) => (
            <li
              key={option.value}
              id={`${rowId}-${i}`}
              role="option"
              aria-selected={option.value === value}
              className={i === active ? styles.active : undefined}
              // The input keeps the focus, so the press must not steal it.
              onMouseDown={(e) => e.preventDefault()}
              onMouseEnter={() => setActive(i)}
              onClick={() => choose(option)}
            >
              <span className={styles.value}>{option.value}</span>
              {option.label && <span className={styles.label}>{option.label}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
