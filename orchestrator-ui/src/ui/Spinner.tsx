import styles from './Spinner.module.css';

/** A small ring that turns, in the current text colour. Decorative: the control it sits in says what is busy. */
export function Spinner() {
  return <span className={styles.spinner} aria-hidden="true" data-testid="spinner" />;
}
