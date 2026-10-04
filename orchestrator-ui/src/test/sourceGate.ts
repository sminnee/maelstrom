import { readdirSync } from 'node:fs';
import { join } from 'node:path';

/** The app's source root, the directory each gate walks. */
export const SRC = join(__dirname, '..');

/** Each file under `dir` whose name ends with one of `suffixes`, except the names in `exclude`. */
export function sourceFiles(dir: string, suffixes: string[], exclude: string[] = []): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(path, suffixes, exclude);
    if (exclude.includes(entry.name)) return [];
    return suffixes.some((suffix) => entry.name.endsWith(suffix)) ? [path] : [];
  });
}

/** The source with each block comment blanked, so prose about `12px` is not a finding. */
export const withoutComments = (source: string) =>
  source.replace(/\/\*[\s\S]*?\*\//g, (comment) => comment.replace(/[^\n]/g, ' '));

/** Each line `offends` says is an offence, as `line: text`. Line numbers survive the blanking. */
export const lineOffences = (source: string, offends: (line: string) => boolean): string[] =>
  withoutComments(source)
    .split('\n')
    .flatMap((line, i) => (offends(line) ? [`${i + 1}: ${line.trim()}`] : []));
