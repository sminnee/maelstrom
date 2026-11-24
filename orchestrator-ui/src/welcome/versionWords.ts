import type { SystemBody } from '../api/system';

/** The release that runs, as the Version entry says it. */
export function versionWords({ version }: SystemBody): string {
  if (!version) return 'development build';
  return `${version.tag} (${version.commit.slice(0, 12)}${version.dirty ? ', dirty' : ''})`;
}
