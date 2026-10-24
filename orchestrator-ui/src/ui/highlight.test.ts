import { describe, expect, it } from 'vitest';
import { GRAMMARS, highlightLines, type Lang, languageFor } from './highlight';

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

  it('reads a Swift file, and a C header as C++', () => {
    expect(languageFor('App/View.swift')).toBe('swift');
    expect(languageFor('src/main.h')).toBe('cpp');
  });

  it('takes the last extension, so a Kotlin build script is Kotlin', () => {
    expect(languageFor('build.gradle.kts')).toBe('kotlin');
  });

  it('reads any Dockerfile.* and any .env.* by its name', () => {
    expect(languageFor('Dockerfile.dev')).toBe('docker');
    expect(languageFor('web.Dockerfile')).toBe('docker');
    expect(languageFor('.env')).toBe('dotenv');
    expect(languageFor('.env.local')).toBe('dotenv');
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

  it.each(Object.keys(GRAMMARS) as Lang[])('loads and compiles the %s grammar', async (lang) => {
    const tokens = await highlightLines(['x'], lang);
    expect(tokens.map((line) => line.map((t) => t.content).join(''))).toEqual(['x']);
  });
});
