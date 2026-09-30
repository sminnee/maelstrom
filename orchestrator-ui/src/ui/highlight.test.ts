import { describe, expect, it } from 'vitest';
import { highlightLines, languageFor } from './highlight';

describe('languageFor', () => {
  it('reads the extension in any case', () => {
    expect(languageFor('src/App.tsx')).toBe('tsx');
    expect(languageFor('docs/README.MD')).toBe('markdown');
  });

  it('takes a file named in full over its extension', () => {
    expect(languageFor('docker/Dockerfile')).toBe('docker');
  });

  it('gives null for an unknown extension, no extension, or a dotfile', () => {
    expect(languageFor('assets/logo.xyz')).toBeNull();
    expect(languageFor('LICENSE')).toBeNull();
    expect(languageFor('config/.bash')).toBeNull();
  });

  it('gives null for a name an object inherits', () => {
    expect(languageFor('constructor')).toBeNull();
    expect(languageFor('a.toString')).toBeNull();
  });
});

describe('highlightLines', () => {
  it('gives one token list per line, and keeps a comment open across lines', async () => {
    const lines = ['const a = 1;', '/* open', 'still comment */', 'const b = 2;'];
    const tokens = await highlightLines(lines, 'typescript');

    expect(tokens).toHaveLength(4);
    expect(tokens.map((line) => line.map((t) => t.content).join(''))).toEqual(lines);
    const comment = tokens[1]?.[0]?.color;
    expect(comment).toBe('var(--syntax-token-comment)');
    expect(tokens[2]?.[0]?.color).toBe(comment);
    expect(tokens[3]?.[0]?.color).not.toBe(comment);
  });
});
