import { memo } from 'react';
import ReactMarkdown, { defaultUrlTransform, type Components } from 'react-markdown';
import type { PluggableList } from 'unified';
import rehypeSlug from 'rehype-slug';
import remarkGfm from 'remark-gfm';
import { isLinkUri } from '../protocol/linkUri';
import { LinkCard } from './LinkCard';
import { ImageLightbox } from '../ui/ImageLightbox';
import { TableScroll } from '../ui/TableScroll';
import { QuietBlock } from './QuietBlock';
import styles from './Markdown.module.css';

type Attention = 'high' | 'low';
type Segment = { attention: Attention; source: string };

/** A tag at line start. Text after it on the same line opens the new segment. */
const ATTENTION_TAG = /^ {0,3}<user-attention(?:\s+([^>]*))?>[ \t]*(.*)$/;
const FENCE = /^ {0,3}(`{3,}|~{3,})/;

function closesFence(line: string, fence: { char: string; length: number }): boolean {
  return new RegExp(`^ {0,3}${fence.char}{${fence.length},}[\\t ]*$`).test(line);
}

/** Split on line-start attention tags without interpreting tags inside code fences. */
export function attentionSegments(source: string): Segment[] {
  const segments: Segment[] = [];
  let attention: Attention = 'high';
  let lines: string[] = [];
  let fence: { char: string; length: number } | null = null;
  const push = () => {
    if (lines.length) segments.push({ attention, source: lines.join('\n') });
    lines = [];
  };

  const read = (line: string): void => {
    const fenceText = line.match(FENCE)?.[1];
    if (fence) {
      lines.push(line);
      if (closesFence(line, fence)) {
        fence = null;
      }
      return;
    }
    if (fenceText) {
      fence = { char: fenceText.charAt(0), length: fenceText.length };
      lines.push(line);
      return;
    }
    const tag = line.match(ATTENTION_TAG);
    if (tag) {
      push();
      attention = tag[1] === 'low' ? 'low' : 'high';
      // The text after the tag is a line of its own: it can open a fence or hold a tag.
      const rest = tag[2] ?? '';
      if (rest.trim()) read(rest);
      return;
    }
    lines.push(line);
  };

  for (const line of source.split('\n')) read(line);
  push();
  return segments;
}

/** The containers a browser plays. */
const VIDEO_PATH = /\.(webm|mp4|mov)$/i;

/** Whether `src` names a video, read off its path so a query string does not hide it. */
function isVideo(src: string): boolean {
  return VIDEO_PATH.test(src.split(/[?#]/, 1)[0] ?? '');
}

/**
 * Keeps a `mael:` link, which react-markdown's default drops as an unknown
 * scheme. Every other URL takes the default's rule.
 */
function urlTransform(url: string): string {
  return isLinkUri(url) ? url : defaultUrlTransform(url);
}

/** A `mael:` link is a **Link card**. Every other link stays an ordinary one. */
const linkComponent: Components['a'] = ({ node, ...anchor }) => {
  // The hast node is not an attribute. Every other prop is: a footnote's link carries
  // its id and its label.
  void node;
  return isLinkUri(anchor.href) ? (
    <LinkCard uri={anchor.href} label={anchor.children} />
  ) : (
    <a {...anchor} />
  );
};

/** Maps an image ref in the source to the URL to load it from. */
export type ResolveSrc = (src: string) => string;

/**
 * Memoised on its props. A partial message re-renders the transcript a few
 * times a second, and a card whose text did not change skips the parse.
 */
const MarkdownContent = memo(function MarkdownContent({
  source,
  resolveSrc,
  headingIds,
}: {
  source: string;
  resolveSrc?: ResolveSrc;
  headingIds?: boolean;
}) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={headingIds ? REHYPE_SLUG : undefined}
      urlTransform={urlTransform}
      components={{
        a: linkComponent,
        img: ({ src: ref, alt }) => {
          if (typeof ref !== 'string') return null;
          const src = resolveSrc ? resolveSrc(ref) : ref;
          if (isVideo(src)) {
            // `metadata`, so a document with several recordings loads none whole.
            return (
              <video
                className={styles.video}
                src={src}
                controls
                preload="metadata"
                aria-label={alt || 'Video'}
              />
            );
          }
          return <ImageLightbox src={src} alt={alt ?? ''} />;
        },
        // `node` has no parent, so Markdown.module.css keeps a nested table in
        // its column.
        table: ({ children }) => (
          <TableScroll className={styles.tableScroll}>
            <table>{children}</table>
          </TableScroll>
        ),
      }}
    >
      {source}
    </ReactMarkdown>
  );
});

/** Before each heading id, so a heading such as `Root` cannot take an id the app uses. */
export const HEADING_ID_PREFIX = 'doc-';

/** Stable, so the memo does not re-parse. */
const REHYPE_SLUG: PluggableList = [[rehypeSlug, { prefix: HEADING_ID_PREFIX }]];

/** Render agent markdown, including its user-attention ranks. */
export function Markdown({
  source,
  className,
  resolveSrc,
  headingIds,
  ...rest
}: {
  source: string;
  className?: string;
  /** Maps each image ref; pass a stable function, or the memo re-parses. */
  resolveSrc?: ResolveSrc;
  /**
   * Give each heading its GitHub slug as an id, after `HEADING_ID_PREFIX`,
   * as the server's fragments name it. Off by default: a page of many messages would repeat ids. Each
   * attention segment is its own tree, so a duplicate heading in a later
   * segment restarts its `-1` count; documents rarely carry attention tags.
   */
  headingIds?: boolean;
} & Omit<React.HTMLAttributes<HTMLDivElement>, 'className' | 'children'>) {
  return (
    <div className={[styles.markdown, className].filter(Boolean).join(' ')} {...rest}>
      {attentionSegments(source).map((segment, index) =>
        segment.attention === 'low' ? (
          <QuietBlock key={index}>
            <MarkdownContent
              source={segment.source}
              resolveSrc={resolveSrc}
              headingIds={headingIds}
            />
          </QuietBlock>
        ) : (
          <MarkdownContent
            key={index}
            source={segment.source}
            resolveSrc={resolveSrc}
            headingIds={headingIds}
          />
        ),
      )}
    </div>
  );
}
