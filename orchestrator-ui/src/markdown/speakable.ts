import remarkGfm from 'remark-gfm';
import remarkParse from 'remark-parse';
import { unified } from 'unified';
import { attentionSegments } from './Markdown';

const parser = unified().use(remarkParse).use(remarkGfm);

/** The mdast fields the walk reads. */
interface Node {
  type: string;
  value?: string;
  children?: Node[];
}

/** Nodes a listener cannot follow, so the voice leaves them out whole. */
const UNSPOKEN = new Set([
  'code',
  'inlineCode',
  'table',
  'image',
  'imageReference',
  'html',
  'definition',
  'footnoteDefinition',
  'footnoteReference',
  'thematicBreak',
]);

/**
 * The text to speak for one message: its `high` parts as plain prose.
 *
 * Code, tables, images and HTML drop out. A link keeps its text, not its URL.
 * Heading and emphasis markup drop out, their words stay. `""` when nothing
 * is left, so the caller skips the message.
 */
export function speakableText(markdown: string): string {
  const parts = attentionSegments(markdown)
    .filter((segment) => segment.attention === 'high')
    .map((segment) => blocksOf(parser.parse(segment.source) as Node).join('\n\n'));
  return parts.filter(Boolean).join('\n\n').trim();
}

/** Each block of `node`'s spoken text, in order. */
function blocksOf(node: Node): string[] {
  if (UNSPOKEN.has(node.type)) return [];
  if (node.type === 'paragraph' || node.type === 'heading') {
    const text = (node.children ?? [])
      .map(inline)
      .join('')
      .replace(/\s+/g, ' ')
      // A dropped code span leaves a gap before its punctuation.
      .replace(/ ([.,;:!?])/g, '$1')
      .trim();
    return text ? [text] : [];
  }
  return (node.children ?? []).flatMap(blocksOf);
}

/** The words of an inline run, markup dropped. */
function inline(node: Node): string {
  if (UNSPOKEN.has(node.type)) return '';
  if (node.type === 'text') return node.value ?? '';
  if (node.type === 'break') return ' ';
  return (node.children ?? []).map(inline).join('');
}
