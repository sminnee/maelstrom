/** A desk with an arrow coming down at it: "puts on the desk". Decorative; the button names itself. */
export function OnDeskIcon({ className }: { className?: string }) {
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
      <path d="M6 1.2v4.8M3.8 3.8 6 6l2.2-2.2" />
    </svg>
  );
}
