/** A terminal window: "opens a shell". Decorative; the link names itself. */
export function TerminalIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      width="12"
      height="12"
      viewBox="0 0 12 12"
      aria-hidden="true"
      focusable="false"
    >
      <rect
        x="1.5"
        y="2"
        width="9"
        height="8"
        rx="1"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
      />
      <path
        d="M3.5 5 5 6.25 3.5 7.5M6 7.5h2.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
