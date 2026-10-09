import { useLayoutEffect, useRef, type ComponentPropsWithoutRef } from 'react';
import styles from './TextArea.module.css';

/**
 * A `<textarea>`. With `grow`, its height follows its text, so the container
 * scrolls, not the field. To cap the growth, the caller's class sets
 * `max-height` and `overflow-y: auto`; past the cap the field scrolls itself.
 *
 * Where the browser supports `field-sizing: content`, it fits the height and
 * the script fit does not run. See DESIGN.md, "The Still Screen Rule".
 */
export function TextArea({
  grow = false,
  className,
  style,
  ...props
}: ComponentPropsWithoutRef<'textarea'> & { grow?: boolean }) {
  const ref = useRef<HTMLTextAreaElement>(null);

  // A layout effect, so no frame paints the old height.
  useLayoutEffect(() => {
    if (grow && !sizesItself()) fitToText(ref.current);
  }, [grow, props.value]);

  // A new width re-wraps the text: a resized panel, a rotated phone.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!grow || !el || sizesItself() || typeof ResizeObserver !== 'function') return;
    let width = el.clientWidth;
    const observer = new ResizeObserver(() => {
      // The height this sets fires the observer too. Only a width change refits.
      if (el.clientWidth === width) return;
      width = el.clientWidth;
      fitToText(el);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [grow]);

  const classes = [grow ? styles.grow : undefined, className].filter(Boolean).join(' ');
  // `TextArea.module.css` reads the rows for its floor.
  const rows = grow && props.rows ? { '--rows': props.rows } : undefined;
  return (
    <textarea
      ref={ref}
      className={classes || undefined}
      style={rows ? { ...rows, ...style } : style}
      {...props}
    />
  );
}

/** Whether the browser fits a field to its text, from `TextArea.module.css`. */
const sizesItself = () => CSS.supports('field-sizing', 'content');

/** Set a textarea's height to the height of its text. */
function fitToText(el: HTMLTextAreaElement | null) {
  if (!el) return;
  // The collapse below shortens every container, which clamps a scrolled one:
  // typing at the end of a long draft would jump the view.
  const scrolled: [Element, number][] = [];
  for (let p = el.parentElement; p; p = p.parentElement) {
    if (p.scrollTop > 0) scrolled.push([p, p.scrollTop]);
  }
  // Collapse first, or the height only ever grows: `scrollHeight` includes
  // whatever height is already set.
  el.style.height = 'auto';
  // `scrollHeight` stops inside the border, and the app's border-box height
  // includes it.
  const style = getComputedStyle(el);
  const border = parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth) || 0;
  el.style.height = `${el.scrollHeight + border}px`;
  for (const [p, top] of scrolled) p.scrollTop = top;
}
