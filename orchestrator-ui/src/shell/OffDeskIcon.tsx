/** A tray with an arrow going up and out of it: "takes off the desk". Decorative; the button names itself. */
export function OffDeskIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      width="12"
      height="12"
      viewBox="0 0 12 12"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M2 8v2h8V8M6 7V1.5M3.8 3.7 6 1.5l2.2 2.2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
