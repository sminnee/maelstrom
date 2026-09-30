import type { HighlighterCore } from 'shiki/core';

/** One coloured run of a line. `color` is a `var(--syntax-*)` that `tokens.css` sets. */
export interface Token {
  content: string;
  color?: string;
}

/** Each grammar is its own chunk, fetched the first time a diff in that language draws. */
const GRAMMARS = {
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
]);
/** Files named in full, which win over their extension. */
const BY_NAME = new Map<string, Lang>([['Dockerfile', 'docker']]);

/** The language a file's path names, or null when there is none to highlight. */
export function languageFor(path: string): Lang | null {
  const name = path.slice(path.lastIndexOf('/') + 1);
  const named = BY_NAME.get(name);
  if (named) return named;
  // A dotfile such as `.env` has a name and no extension.
  const dot = name.lastIndexOf('.');
  if (dot <= 0) return null;
  return BY_EXTENSION.get(name.slice(dot + 1).toLowerCase()) ?? null;
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
