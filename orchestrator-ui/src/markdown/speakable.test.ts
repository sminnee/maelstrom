import { describe, expect, it } from 'vitest';
import { speakableText } from './speakable';

describe('speakableText', () => {
  it('keeps only the high parts', () => {
    const md = [
      '<user-attention high>',
      'The rebase is clean.',
      '',
      '<user-attention low>',
      'Ran git log to confirm.',
      '',
      '<user-attention high>',
      'Merge it?',
    ].join('\n');
    expect(speakableText(md)).toBe('The rebase is clean.\n\nMerge it?');
  });

  it('reads an untagged message as high', () => {
    expect(speakableText('Done.')).toBe('Done.');
  });

  it('drops code fences and inline code', () => {
    const md = 'Run the gate:\n\n```bash\ntangier gate run\n```\n\nThen push `main`.';
    expect(speakableText(md)).toBe('Run the gate:\n\nThen push.');
  });

  it('drops tables, images and HTML', () => {
    const md = [
      'Before.',
      '',
      '| a | b |',
      '|---|---|',
      '| 1 | 2 |',
      '',
      '![a shot](shot.png)',
      '',
      '<div>raw</div>',
      '',
      'After.',
    ].join('\n');
    expect(speakableText(md)).toBe('Before.\n\nAfter.');
  });

  it('keeps a link text but not its URL', () => {
    expect(speakableText('See [the PR](https://github.com/o/r/pull/118).')).toBe('See the PR.');
  });

  it('drops heading and emphasis markup', () => {
    expect(speakableText('## Result\n\nIt **works**, _mostly_.')).toBe(
      'Result\n\nIt works, mostly.',
    );
  });

  it('speaks each list item', () => {
    expect(speakableText('- one\n- two')).toBe('one\n\ntwo');
  });

  it('returns empty when only low parts and code are left', () => {
    expect(speakableText('<user-attention low>\nDetail.')).toBe('');
    expect(speakableText('```\ncode\n```')).toBe('');
  });
});
