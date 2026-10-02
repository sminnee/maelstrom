/** A drawn folder, shut or open: "a directory, folded or not". Decorative; the control names itself. */
export function FolderIcon({ open, className }: { open: boolean; className?: string }) {
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
        d={
          open
            ? 'M1.5 9.5V2.5H4.5L5.5 3.8H9.5V5.5M1.5 9.5 3 5.5H11L9.5 9.5Z'
            : 'M1.5 2.5H4.5L5.5 3.8H10.5V9.5H1.5Z'
        }
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
