import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ImageLightbox } from '../ui/ImageLightbox';
import { QuietBlock } from './QuietBlock';
import styles from './Markdown.module.css';

type Attention = 'high' | 'low';
type Segment = { attention: Attention; source: string };

const ATTENTION_TAG = /^<user-attention(?:\s+([^>]*))?>\s*$/;
const FENCE = /^ {0,3}(`{3,}|~{3,})/;

function closesFence(line: string, fence: { char: string; length: number }): boolean {
  return new RegExp(`^ {0,3}${fence.char}{${fence.length},}[\\t ]*$`).test(line);
}

/** Split standalone attention tags without interpreting tags inside code fences. */
export function attentionSegments(source: string): Segment[] {
  const segments: Segment[] = [];
  let attention: Attention = 'high';
  let lines: string[] = [];
  let fence: { char: string; length: number } | null = null;
  const push = () => {
    if (lines.length) segments.push({ attention, source: lines.join('\n') });
    lines = [];
  };

  for (const line of source.split('\n')) {
    const fenceText = line.match(FENCE)?.[1];
    if (fence) {
      lines.push(line);
      if (closesFence(line, fence)) {
        fence = null;
      }
      continue;
    }
    if (fenceText) {
      fence = { char: fenceText.charAt(0), length: fenceText.length };
      lines.push(line);
      continue;
    }
    const tag = line.match(ATTENTION_TAG);
    if (tag) {
      push();
      attention = tag[1] === 'low' ? 'low' : 'high';
      continue;
    }
    lines.push(line);
  }
  push();
  return segments;
}

function MarkdownContent({ source }: { source: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        img: ({ src, alt }) =>
          typeof src === 'string' ? <ImageLightbox src={src} alt={alt ?? ''} /> : null,
      }}
    >
      {source}
    </ReactMarkdown>
  );
}

/** Render agent markdown, including its user-attention ranks. */
export function Markdown({
  source,
  className,
  ...rest
}: {
  source: string;
  className?: string;
} & Omit<React.HTMLAttributes<HTMLDivElement>, 'className' | 'children'>) {
  return (
    <div className={[styles.markdown, className].filter(Boolean).join(' ')} {...rest}>
      {attentionSegments(source).map((segment, index) =>
        segment.attention === 'low' ? (
          <QuietBlock key={index}>
            <MarkdownContent source={segment.source} />
          </QuietBlock>
        ) : (
          <MarkdownContent key={index} source={segment.source} />
        ),
      )}
    </div>
  );
}
