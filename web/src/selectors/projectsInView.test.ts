import { describe, expect, it } from 'vitest';
import { projectsInView } from './projectsInView';
import { noFilters } from './filters';
import { makeAgent, makeProject, makeTask, onDesk, worldWith } from '../test/fixtures';

describe('projectsInView', () => {
  /** A world whose every task is drawn, which is what the canvas shows. */
  function drawn(parts: Parameters<typeof worldWith>[0]) {
    return worldWith({ ...parts, desk: parts.desk ?? onDesk(parts.tasks ?? []) });
  }

  const world = drawn({
    projects: [
      makeProject({ id: 'northwind' }),
      makeProject({ id: 'maelstrom' }),
      makeProject({ id: 'riverbend' }),
    ],
    tasks: [
      makeTask({ id: 'T1', project: 'northwind', branch: 'feat/orders' }),
      makeTask({ id: 'T2', project: 'maelstrom', branch: 'feat/ui' }),
    ],
  });

  it('names the projects the drawn nodes belong to, sorted', () => {
    // `riverbend` has a project but nothing drawn, so it is not in view.
    expect(projectsInView(world, noFilters())).toEqual(['maelstrom', 'northwind']);
  });

  it('narrows to one project when the filter bar names one', () => {
    expect(projectsInView(world, { ...noFilters(), project: 'maelstrom' })).toEqual(['maelstrom']);
  });

  it('counts a free agent, which is drawn in its own right', () => {
    // A project whose only work is an agent with no task is still in view: the
    // node is on the canvas, so the radios must offer it.
    const withAgent = worldWith({
      projects: [makeProject({ id: 'riverbend' })],
      agents: [makeAgent({ id: 'free-1', taskId: '', project: 'riverbend' })],
    });
    expect(projectsInView(withAgent, noFilters())).toEqual(['riverbend']);
  });

  it('names nothing when the canvas draws nothing', () => {
    // The caller falls back to every project on this, rather than offering an
    // empty fieldset.
    expect(projectsInView(worldWith({ projects: [makeProject()] }), noFilters())).toEqual([]);
  });
});
