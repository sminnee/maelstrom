import { useMemo, useState } from 'react';
import type { ComboOption } from './ComboBox';
import styles from './MultiComboBox.module.css';
import { useComboBoxOffer } from './useComboBoxOffer';

/**
 * A text field that picks many values from a list, shown as removable chips.
 *
 * Unlike `ComboBox`, the typed text is never itself a value: there is no
 * single free-text field to keep, so a choice must come from the list. Picking
 * a row appends it and clears the filter, keeping the offer open for the next
 * pick — repeated choosing, not choose-and-close.
 *
 * An option already chosen drops out of the offer, so the list only ever
 * shows what is still choosable.
 */
export function MultiComboBox({
  value,
  options,
  onChange,
  id,
  placeholder,
  readOnly,
}: {
  value: readonly string[];
  options: readonly ComboOption[];
  onChange: (value: string[]) => void;
  id?: string;
  placeholder?: string;
  /** Shows the chips without offering to change them. The offer never opens. */
  readOnly?: boolean;
}) {
  const [text, setText] = useState('');

  const byLabel = useMemo(() => new Map(options.map((o) => [o.value, o])), [options]);

  const offered = useMemo(() => {
    const needle = text.trim().toLowerCase();
    return options.filter((o) => {
      if (value.includes(o.value)) return false;
      if (!needle) return true;
      return (
        o.value.toLowerCase().includes(needle) || (o.label ?? '').toLowerCase().includes(needle)
      );
    });
  }, [options, value, text]);

  const {
    open,
    setOpen,
    active,
    setActive,
    showing,
    activeId,
    listId,
    rowId,
    box,
    list,
    input,
    anchorStyle,
    place,
  } = useComboBoxOffer(offered.length);

  const choose = (option: ComboOption) => {
    onChange([...value, option.value]);
    setText('');
    setActive(-1);
  };

  const remove = (id: string) => onChange(value.filter((v) => v !== id));

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      if (!open) return;
      e.stopPropagation();
      e.preventDefault();
      setOpen(false);
      setActive(-1);
      return;
    }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!offered.length) return;
      if (readOnly) return;
      setOpen(true);
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
      return;
    }
    // Removes the last chip on Backspace over an empty filter, the way a
    // browser's own multi-value inputs (email `To:`) let a chip be undone
    // without reaching for the mouse.
    if (e.key === 'Backspace' && text === '' && value.length > 0) {
      if (readOnly) return;
      remove(value[value.length - 1]!);
    }
  };

  return (
    <div
      className={styles.box}
      ref={box}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOpen(false);
      }}
    >
      {value.length > 0 && (
        <ul className={styles.chips}>
          {value.map((v) => {
            const option = byLabel.get(v);
            return (
              <li key={v} className={styles.chip}>
                <span>{option?.label ?? v}</span>
                {!readOnly && (
                  <button
                    type="button"
                    className={styles.remove}
                    aria-label={`Remove ${option?.label ?? v}`}
                    onClick={() => remove(v)}
                  >
                    ×
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
      <input
        id={id}
        ref={input}
        style={anchorStyle}
        role="combobox"
        aria-expanded={showing}
        aria-controls={showing ? listId : undefined}
        aria-activedescendant={activeId}
        aria-autocomplete="list"
        autoComplete="off"
        placeholder={placeholder}
        readOnly={readOnly}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setOpen(true);
          setActive(-1);
        }}
        onFocus={() => !readOnly && setOpen(true)}
        onKeyDown={onKeyDown}
      />
      {showing && (
        <ul
          className={styles.list}
          id={listId}
          role="listbox"
          ref={list}
          style={anchorStyle}
          popover="manual"
          onBeforeToggle={(e) => {
            if ((e as unknown as { newState: string }).newState === 'open') place(e.currentTarget);
          }}
        >
          {offered.map((option, i) => (
            <li
              key={option.value}
              id={`${rowId}-${i}`}
              role="option"
              aria-selected={false}
              className={i === active ? styles.active : undefined}
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
