import type { Filters } from './filters';
import { deriveGraph } from './graph';
import type { WorldView } from './world';

/**
 * The projects the canvas is currently drawing, sorted.
 *
 * It calls `deriveGraph` rather than reading the world itself, so the answer
 * follows the filter bar and cannot disagree with what is on screen — the same
 * reason `deriveDeck` calls it. Grouping is fixed at `none`: grouping moves the
 * lanes, never which nodes are drawn.
 *
 * New work offers these as radios, so the common case — one project in view —
 * is one click. An empty answer means the canvas draws nothing, and the caller
 * falls back to offering every project rather than an empty fieldset.
 */
export function projectsInView(world: WorldView, filters: Filters): string[] {
  const graph = deriveGraph(world, { groupBy: 'none', filters });
  const names = new Set<string>();
  for (const node of graph.nodes) {
    // A task names its project; a free agent's comes from the agent, else the
    // worktree it runs in, exactly as the filters read it.
    const project = node.task?.project ?? node.agent?.project ?? node.worktree?.project ?? '';
    if (project) names.add(project);
  }
  return [...names].sort();
}
