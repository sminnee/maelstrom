import styles from './DeskIcon.module.css';

/** A desk with an arrow going up from it: "takes off the desk". Decorative; the button names itself. */
export function OffDeskIcon({ className }: { className?: string }) {
  return (
    <svg
      className={[styles.off, className].filter(Boolean).join(' ')}
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
      <path className={styles.arrow} stroke="currentColor" d="M6 6V1.2M3.8 3.4 6 1.2l2.2 2.2" />
    </svg>
  );
}
