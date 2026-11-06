/** `path` with `home` at its start written as `~`. */
export function tildePath(path: string, home: string | undefined): string {
  if (!home) return path;
  if (path === home) return '~';
  return path.startsWith(`${home}/`) ? `~${path.slice(home.length)}` : path;
}
