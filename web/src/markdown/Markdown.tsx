import { isValidElement } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ImageLightbox } from '../ui/ImageLightbox';
import styles from './Markdown.module.css';

/**
 * The fence body of a ```callout block, or null for any other `pre`.
 *
 * react-markdown hands the `pre` override its `code` child rather than the
 * text, so the language is read off that child's class. Every other fence —
 * including one with no info string, which is the common case in agent prose —
 * must fall through untouched.
 */
function calloutText(children: React.ReactNode): string | null {
  if (!isValidElement(children)) return null;
  const props = children.props as { className?: string; children?: React.ReactNode };
  if (!props.className?.split(' ').includes('language-callout')) return null;
  // An empty fence carries no child at all, and must still be a callout: falling
  // through would dress it in the code chrome a callout exists to replace.
  if (typeof props.children !== 'string') return '';
  return props.children.replace(/\n$/, '');
}

/**
 * How deep a callout may nest before its body is left as a listing.
 *
 * A fence opened with four backticks keeps a three-backtick fence inside it
 * verbatim, so an agent quoting a transcript that already held a callout drives
 * this recursion. Each level re-parses the whole remaining body, so the cost is
 * quadratic. Nothing in DESIGN.md gives a callout inside a callout a meaning.
 */
const MAX_CALLOUT_DEPTH = 2;

/**
 * Rendered markdown with GFM. The one place react-markdown is imported.
 *
 * An image renders as a thumbnail that opens full size. Both an image an agent
 * showed and one the user pasted arrive here as an ordinary markdown ref, so
 * the two directions draw the same way.
 *
 * A ```callout fence is the one thing an agent marks as worth reading, and it
 * renders as prose rather than as a listing — see DESIGN.md, "The Two Ranks of
 * Prose Rule". Its body goes back through this same component, so a callout can
 * carry a literal, a link or emphasis.
 */
export function Markdown({
  source,
  className,
  depth = 0,
  ...rest
}: {
  source: string;
  className?: string;
  /** How many callouts enclose this one. See {@link MAX_CALLOUT_DEPTH}. */
  depth?: number;
} & Omit<React.HTMLAttributes<HTMLDivElement>, 'className' | 'children'>) {
  return (
    <div className={[styles.markdown, className].filter(Boolean).join(' ')} {...rest}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          img: ({ src, alt }) =>
            typeof src === 'string' ? <ImageLightbox src={src} alt={alt ?? ''} /> : null,
          pre: ({ children, ...rest }) => {
            const text = depth < MAX_CALLOUT_DEPTH ? calloutText(children) : null;
            if (text === null) return <pre {...rest}>{children}</pre>;
            return (
              <div className={styles.callout} data-testid="callout">
                <Markdown source={text} depth={depth + 1} />
              </div>
            );
          },
        }}
      >
        {source}
      </ReactMarkdown>
    </div>
  );
}
