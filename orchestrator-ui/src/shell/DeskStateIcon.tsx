import styles from './DeskIcon.module.css';

/** A desk, with a square on it when the task is on the desk. Shows a state, not an act. Decorative; the button names itself. */
export function DeskStateIcon({ onDesk, className }: { onDesk: boolean; className?: string }) {
  return (
    <svg
      className={className}
      width="12"
      height="12"
      viewBox="0 0 12 12"
      aria-hidden="true"
      focusable="false"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M3.6 8h4.8l1.6 2.5H2z" />
      {onDesk && (
        <rect
          className={styles.square}
          x="4.3"
          y="5.85"
          width="3.4"
          height="3.4"
          rx="0.4"
          stroke="none"
        />
      )}
    </svg>
  );
}
