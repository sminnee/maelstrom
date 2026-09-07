import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ImageLightbox } from '../ui/ImageLightbox';
import styles from './Markdown.module.css';

/**
 * Rendered markdown with GFM. The one place react-markdown is imported.
 *
 * An image renders as a thumbnail that opens full size. Both an image an agent
 * showed and one the user pasted arrive here as an ordinary markdown ref, so
 * the two directions draw the same way.
 */
export function Markdown({ source, className }: { source: string; className?: string }) {
  return (
    <div className={[styles.markdown, className].filter(Boolean).join(' ')}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          img: ({ src, alt }) =>
            typeof src === 'string' ? <ImageLightbox src={src} alt={alt ?? ''} /> : null,
        }}
      >
        {source}
      </ReactMarkdown>
    </div>
  );
}
