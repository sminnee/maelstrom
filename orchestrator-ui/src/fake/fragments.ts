import GithubSlugger from 'github-slugger';

const FENCE = /^ {0,3}(`{3,}|~{3,})/;
const HEADING = /^ {0,3}#{1,6}\s+(.*?)\s*#*\s*$/;
const LINK = /\[([^\]]*)\]\([^)]*\)/g;

/**
 * A markdown body's fragments, as `task_documents.fragments` names them: the
 * slug of each ATX heading outside a code fence. `heading-slugs.json` holds
 * the cases, which `fragments.test.ts` checks.
 */
export function fakeFragments(markdown: string): string[] {
  const slugger = new GithubSlugger();
  let fence: string | null = null;
  const slugs: string[] = [];
  for (const line of markdown.split('\n')) {
    const mark = FENCE.exec(line)?.[1]?.charAt(0);
    if (mark) {
      fence = fence === null ? mark : fence === mark ? null : fence;
      continue;
    }
    const heading = fence === null ? HEADING.exec(line) : null;
    if (heading) slugs.push(slugger.slug(heading[1]!.replace(LINK, '$1')));
  }
  return slugs;
}
