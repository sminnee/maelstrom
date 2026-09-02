/** A rectangle with a filled right-hand column: "opens in the panel". Decorative; the link names itself. */
export function OpenInPanelIcon({ className }: { className?: string }) {
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
        x="1"
        y="1.5"
        width="10"
        height="9"
        rx="1.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
      />
      <path d="M7 1.5h3a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H7z" fill="currentColor" />
    </svg>
  );
}
