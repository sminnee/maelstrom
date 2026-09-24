import { AppButton } from '../ui/AppButton';
import { useExpandableClamp } from '../ui/useExpandableClamp';
import styles from './Markdown.module.css';

/** Working detail, clamped to two lines until asked for. */
export function QuietBlock({ children }: { children: React.ReactNode }) {
  const { expanded, collapse, bodyProps } = useExpandableClamp([children]);

  return (
    <div className={styles.quiet} data-testid="quiet">
      <div className={styles.quietBody} {...bodyProps}>
        {children}
      </div>
      {expanded && (
        <AppButton
          variant="link"
          className={styles.quietMore}
          aria-controls={bodyProps.id}
          onClick={collapse}
        >
          Show less
        </AppButton>
      )}
    </div>
  );
}
