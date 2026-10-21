import { describe, expect, it } from 'vitest';
import { diffBlocks, markdownBlocks, markdownDefinitions } from './blocks';

describe('markdownBlocks', () => {
  it('cuts a document at its top-level blocks, each as its source', () => {
    const fence = '```sh\nmael env start\n\nmael env stop\n```';
    const table = '| Key | Value |\n| --- | --- |\n| a | 1 |';
    expect(
      markdownBlocks(`# Title\n\nSome *prose*\nover two lines.\n\n${fence}\n\n${table}\n`),
    ).toEqual(['# Title', 'Some *prose*\nover two lines.', fence, table]);
  });

  it('keeps front matter and HTML as blocks of their own', () => {
    expect(markdownBlocks('---\ntitle: Guide\n---\n\n<details>\n\nBody\n')).toEqual([
      '---\ntitle: Guide\n---',
      '<details>',
      'Body',
    ]);
    // CRLF, and front matter that ends the file.
    expect(markdownBlocks('---\r\ntitle: x\r\n---')).toEqual(['---\r\ntitle: x\r\n---']);
  });
});

describe('markdownDefinitions', () => {
  it('collects the link and footnote definitions, for a block drawn alone', () => {
    const doc = 'See [the guide][g] and a note.[^1]\n\n[g]: https://x/guide\n\n[^1]: The note.\n';
    expect(markdownDefinitions(doc)).toBe('\n\n[g]: https://x/guide\n\n[^1]: The note.');
    expect(markdownDefinitions('# None\n')).toBe('');
    expect(markdownDefinitions(null)).toBe('');
  });
});

describe('diffBlocks', () => {
  it('draws an edited paragraph as removed then added, the rest unchanged', () => {
    expect(diffBlocks('# A\n\nOne.\n\nTwo.\n', '# A\n\nOne!\n\nTwo.\n')).toEqual([
      { kind: 'context', item: '# A' },
      { kind: 'remove', item: 'One.' },
      { kind: 'add', item: 'One!' },
      { kind: 'context', item: 'Two.' },
    ]);
  });

  it('pairs each edited block with its new version when edits touch', () => {
    expect(diffBlocks('One.\n\nTwo.\n\nThree.\n', 'One!\n\nTwo!\n\nAdded.\n\nThree.\n')).toEqual([
      { kind: 'remove', item: 'One.' },
      { kind: 'add', item: 'One!' },
      { kind: 'remove', item: 'Two.' },
      { kind: 'add', item: 'Two!' },
      { kind: 'add', item: 'Added.' },
      { kind: 'context', item: 'Three.' },
    ]);
  });

  it('draws a missing side as every block added or removed', () => {
    expect(diffBlocks(null, '# A\n\nOne.\n')).toEqual([
      { kind: 'add', item: '# A' },
      { kind: 'add', item: 'One.' },
    ]);
  });
});
