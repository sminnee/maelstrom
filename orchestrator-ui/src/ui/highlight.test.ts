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

  it('reads the interpreter from a shebang when the path names no language', () => {
    expect(languageFor('bin/lint', '#!/usr/bin/env bash')).toBe('bash');
    expect(languageFor('bin/lint', '#!/bin/sh')).toBe('bash');
    expect(languageFor('bin/lint', '#!/usr/bin/env -S zsh -e')).toBe('bash');
    expect(languageFor('bin/lint', '#!/usr/bin/env python3')).toBe('python');
    expect(languageFor('bin/lint', '#! /usr/bin/python3.12')).toBe('python');
    expect(languageFor('bin/serve', '#!/usr/bin/env node')).toBe('javascript');
    expect(languageFor('bin/serve', '#!/usr/bin/env FOO=1 ruby')).toBe('ruby');
    expect(languageFor('.bashrc', '#!/bin/bash')).toBe('bash');
  });

  it('takes the extension over the shebang, even an unknown one', () => {
    expect(languageFor('bin/lint.py', '#!/bin/sh')).toBe('python');
    expect(languageFor('bin/deploy.local', '#!/bin/sh')).toBeNull();
  });

  it('gives null for a line that is no shebang, or an unknown interpreter', () => {
    expect(languageFor('bin/lint', 'echo hi')).toBeNull();
    expect(languageFor('bin/lint', '#!/usr/bin/env perl')).toBeNull();
    expect(languageFor('bin/lint', '#!/usr/bin/env')).toBeNull();
    expect(languageFor('bin/lint', null)).toBeNull();
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
