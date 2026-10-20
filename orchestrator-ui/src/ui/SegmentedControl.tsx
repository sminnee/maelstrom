import styles from './SegmentedControl.module.css';

export interface Segment<T extends string> {
  id: T;
  label: string;
  disabled?: boolean;
}

/** A group of toggle buttons, one pressed: a choice of one among a few. */
export function SegmentedControl<T extends string>({
  label,
  items,
  pressed,
  onPress,
}: {
  label: string;
  items: Segment<T>[];
  pressed: T | undefined;
  onPress: (id: T) => void;
}) {
  return (
    <div className={styles.group} role="group" aria-label={label}>
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          className={`${styles.segment} nowrap`}
          aria-pressed={pressed === item.id}
          disabled={item.disabled}
          onClick={() => onPress(item.id)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}
