/** A tray with an arrow coming down into it: "puts on the desk". Decorative; the button names itself. */
export function OnDeskIcon({ className }: { className?: string }) {
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
        d="M2 8v2h8V8M6 1.5v5.5M3.8 4.8 6 7l2.2-2.2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
