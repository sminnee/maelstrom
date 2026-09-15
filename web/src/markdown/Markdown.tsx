import { isValidElement } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ImageLightbox } from '../ui/ImageLightbox';
import styles from './Markdown.module.css';

/**
 * The fence body of a ```quiet block, or null for any other `pre`.
 *
 * react-markdown hands the `pre` override its `code` child rather than the
 * text, so the language is read off that child's class. Every other fence —
 * including one with no info string, which is the common case in agent prose —
 * must fall through untouched.
 */
function quietText(children: React.ReactNode): string | null {
  if (!isValidElement(children)) return null;
  const props = children.props as { className?: string; children?: React.ReactNode };
  if (!props.className?.split(' ').includes('language-quiet')) return null;
  // An empty fence carries no child at all, and must still be quiet: falling
  // through would dress it in the code chrome a quiet block exists to replace.
  if (typeof props.children !== 'string') return '';
  return props.children.replace(/\n$/, '');
}

// Nested fences can make each quiet body parse the remainder again.
const MAX_QUIET_DEPTH = 2;

/**
 * Rendered markdown with GFM. The one place react-markdown is imported.
 *
 * An image renders as a thumbnail that opens full size. Both an image an agent
 * showed and one the user pasted arrive here as an ordinary markdown ref, so
 * the two directions draw the same way.
 *
 * A ```quiet fence marks self-talk. It renders as quiet prose rather than as a
 * listing. Its body goes back through this same component, so it can carry a
 * literal, a link or emphasis.
 */
export function Markdown({
  source,
  className,
  quietDepth = 0,
  ...rest
}: {
  source: string;
  className?: string;
  /** How many quiet blocks enclose this one. */
  quietDepth?: number;
} & Omit<React.HTMLAttributes<HTMLDivElement>, 'className' | 'children'>) {
  return (
    <div className={[styles.markdown, className].filter(Boolean).join(' ')} {...rest}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          img: ({ src, alt }) =>
            typeof src === 'string' ? <ImageLightbox src={src} alt={alt ?? ''} /> : null,
          pre: ({ children, ...rest }) => {
            const text = quietDepth < MAX_QUIET_DEPTH ? quietText(children) : null;
            if (text === null) return <pre {...rest}>{children}</pre>;
            return (
              <div className={styles.quiet} data-testid="quiet">
                <Markdown source={text} quietDepth={quietDepth + 1} />
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
