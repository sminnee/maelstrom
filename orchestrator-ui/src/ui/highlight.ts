import type { HighlighterCore } from 'shiki/core';

/** One coloured run of a line. `color` is a `var(--syntax-*)` that `tokens.css` sets. */
export interface Token {
  content: string;
  color?: string;
}

/** Each grammar is its own chunk, fetched the first time a diff in that language draws. */
export const GRAMMARS = {
  typescript: () => import('shiki/langs/typescript.mjs'),
  tsx: () => import('shiki/langs/tsx.mjs'),
  javascript: () => import('shiki/langs/javascript.mjs'),
  jsx: () => import('shiki/langs/jsx.mjs'),
  json: () => import('shiki/langs/json.mjs'),
  python: () => import('shiki/langs/python.mjs'),
  css: () => import('shiki/langs/css.mjs'),
  html: () => import('shiki/langs/html.mjs'),
  markdown: () => import('shiki/langs/markdown.mjs'),
  yaml: () => import('shiki/langs/yaml.mjs'),
  toml: () => import('shiki/langs/toml.mjs'),
  bash: () => import('shiki/langs/bash.mjs'),
  sql: () => import('shiki/langs/sql.mjs'),
  gherkin: () => import('shiki/langs/gherkin.mjs'),
  go: () => import('shiki/langs/go.mjs'),
  rust: () => import('shiki/langs/rust.mjs'),
  docker: () => import('shiki/langs/docker.mjs'),
  swift: () => import('shiki/langs/swift.mjs'),
  'objective-c': () => import('shiki/langs/objective-c.mjs'),
  'objective-cpp': () => import('shiki/langs/objective-cpp.mjs'),
  c: () => import('shiki/langs/c.mjs'),
  cpp: () => import('shiki/langs/cpp.mjs'),
  csharp: () => import('shiki/langs/csharp.mjs'),
  java: () => import('shiki/langs/java.mjs'),
  kotlin: () => import('shiki/langs/kotlin.mjs'),
  ruby: () => import('shiki/langs/ruby.mjs'),
  php: () => import('shiki/langs/php.mjs'),
  scss: () => import('shiki/langs/scss.mjs'),
  less: () => import('shiki/langs/less.mjs'),
  xml: () => import('shiki/langs/xml.mjs'),
  ini: () => import('shiki/langs/ini.mjs'),
  dotenv: () => import('shiki/langs/dotenv.mjs'),
  make: () => import('shiki/langs/make.mjs'),
  diff: () => import('shiki/langs/diff.mjs'),
  graphql: () => import('shiki/langs/graphql.mjs'),
  lua: () => import('shiki/langs/lua.mjs'),
  vue: () => import('shiki/langs/vue.mjs'),
  svelte: () => import('shiki/langs/svelte.mjs'),
  fish: () => import('shiki/langs/fish.mjs'),
  powershell: () => import('shiki/langs/powershell.mjs'),
  hcl: () => import('shiki/langs/hcl.mjs'),
  terraform: () => import('shiki/langs/terraform.mjs'),
  proto: () => import('shiki/langs/proto.mjs'),
  jsonc: () => import('shiki/langs/jsonc.mjs'),
  jsonl: () => import('shiki/langs/jsonl.mjs'),
  nix: () => import('shiki/langs/nix.mjs'),
  just: () => import('shiki/langs/just.mjs'),
};

export type Lang = keyof typeof GRAMMARS;

const BY_EXTENSION = new Map<string, Lang>([
  ['ts', 'typescript'],
  ['mts', 'typescript'],
  ['cts', 'typescript'],
  ['tsx', 'tsx'],
  ['js', 'javascript'],
  ['mjs', 'javascript'],
  ['cjs', 'javascript'],
  ['jsx', 'jsx'],
  ['json', 'json'],
  ['py', 'python'],
  ['css', 'css'],
  ['html', 'html'],
  ['md', 'markdown'],
  ['yaml', 'yaml'],
  ['yml', 'yaml'],
  ['toml', 'toml'],
  ['sh', 'bash'],
  ['bash', 'bash'],
  ['zsh', 'bash'],
  ['sql', 'sql'],
  ['feature', 'gherkin'],
  ['go', 'go'],
  ['rs', 'rust'],
  ['dockerfile', 'docker'],
  ['swift', 'swift'],
  ['m', 'objective-c'],
  ['mm', 'objective-cpp'],
  ['c', 'c'],
  // C, C++ and Objective-C share `.h`; the C++ grammar colours all three well enough.
  ['h', 'cpp'],
  ['cc', 'cpp'],
  ['cpp', 'cpp'],
  ['cxx', 'cpp'],
  ['hh', 'cpp'],
  ['hpp', 'cpp'],
  ['hxx', 'cpp'],
  ['cs', 'csharp'],
  ['java', 'java'],
  ['kt', 'kotlin'],
  ['kts', 'kotlin'],
  ['rb', 'ruby'],
  ['php', 'php'],
  ['scss', 'scss'],
  ['less', 'less'],
  ['xml', 'xml'],
  ['svg', 'xml'],
  ['plist', 'xml'],
  ['ini', 'ini'],
  ['cfg', 'ini'],
  ['mk', 'make'],
  ['diff', 'diff'],
  ['patch', 'diff'],
  ['graphql', 'graphql'],
  ['gql', 'graphql'],
  ['lua', 'lua'],
  ['vue', 'vue'],
  ['svelte', 'svelte'],
  ['fish', 'fish'],
  ['ps1', 'powershell'],
  ['hcl', 'hcl'],
  ['tf', 'terraform'],
  ['proto', 'proto'],
  ['jsonc', 'jsonc'],
  ['jsonl', 'jsonl'],
  ['nix', 'nix'],
]);
/** Files named in full, which win over their extension. */
const BY_NAME = new Map<string, Lang>([
  ['Dockerfile', 'docker'],
  ['Gemfile', 'ruby'],
  ['Rakefile', 'ruby'],
  ['Makefile', 'make'],
  ['justfile', 'just'],
  ['.env', 'dotenv'],
]);
/** The interpreter a `#!` line names, without its version suffix. */
const BY_INTERPRETER = new Map<string, Lang>([
  ['sh', 'bash'],
  ['bash', 'bash'],
  ['zsh', 'bash'],
  ['dash', 'bash'],
  ['ksh', 'bash'],
  ['fish', 'fish'],
  ['python', 'python'],
  ['node', 'javascript'],
  ['deno', 'typescript'],
  ['bun', 'typescript'],
  ['tsx', 'typescript'],
  ['ts-node', 'typescript'],
  ['ruby', 'ruby'],
  ['php', 'php'],
  ['lua', 'lua'],
  ['pwsh', 'powershell'],
]);

/**
 * The language a file's path names, or null when there is none to highlight.
 * A name with no extension falls back to the interpreter its `#!` line names.
 */
export function languageFor(path: string, shebang?: string | null): Lang | null {
  const name = path.slice(path.lastIndexOf('/') + 1);
  const named = BY_NAME.get(name);
  if (named) return named;
  if (name.startsWith('Dockerfile.')) return 'docker';
  if (name.startsWith('.env.')) return 'dotenv';
  // A dotfile such as `.bashrc` has a name and no extension.
  const dot = name.lastIndexOf('.');
  if (dot > 0) return BY_EXTENSION.get(name.slice(dot + 1).toLowerCase()) ?? null;
  return shebang ? interpreterLanguage(shebang) : null;
}

/** `#!/bin/sh`, `#!/usr/bin/env python3` and `#!/usr/bin/env -S zsh -e` name sh, python3 and zsh. */
function interpreterLanguage(shebang: string): Lang | null {
  if (!shebang.startsWith('#!')) return null;
  let words = shebang.slice(2).trim().split(/\s+/);
  let program = words[0]?.slice(words[0].lastIndexOf('/') + 1);
  if (program === 'env') {
    // Skip env's flags and its NAME=value settings.
    words = words.slice(1).filter((word) => !word.startsWith('-') && !word.includes('='));
    program = words[0];
  }
  if (!program) return null;
  return BY_INTERPRETER.get(program.replace(/[\d.]+$/, '')) ?? null;
}

let highlighter: Promise<HighlighterCore> | null = null;
const grammars = new Map<Lang, Promise<void>>();

/**
 * One highlighter for the app, with the grammar `lang` needs. Shiki's core is
 * a chunk of its own, so a page that draws no diff never fetches it. A failed
 * load is forgotten, so the next diff tries again rather than staying plain
 * until a reload.
 */
async function highlighterFor(lang: Lang): Promise<HighlighterCore> {
  if (!highlighter) {
    highlighter = (async () => {
      const [{ createHighlighterCore, createCssVariablesTheme }, { createJavaScriptRegexEngine }] =
        await Promise.all([import('shiki/core'), import('shiki/engine/javascript')]);
      return createHighlighterCore({
        themes: [createCssVariablesTheme({ name: 'syntax', variablePrefix: '--syntax-' })],
        langs: [],
        engine: createJavaScriptRegexEngine(),
      });
    })();
    highlighter.catch(() => (highlighter = null));
  }
  const shiki = await highlighter;
  let grammar = grammars.get(lang);
  if (!grammar) {
    grammar = GRAMMARS[lang]().then((module) => shiki.loadLanguage(module));
    grammar.catch(() => grammars.delete(lang));
    grammars.set(lang, grammar);
  }
  await grammar;
  return shiki;
}

/**
 * The tokens of each line. The lines are highlighted as one text, so a
 * comment or a string that spans lines keeps its colour on each of them.
 */
export async function highlightLines(lines: string[], lang: Lang): Promise<Token[][]> {
  const shiki = await highlighterFor(lang);
  const { tokens } = shiki.codeToTokens(lines.join('\n'), { lang, theme: 'syntax' });
  return tokens.map((line) => line.map(({ content, color }) => ({ content, color })));
}
