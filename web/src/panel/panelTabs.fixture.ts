import type { PanelTab } from '../store/uiSlice';
import { documentTab, sessionTab } from '../selectors/tabs';
import { makeAgent, makeDocument, makeTask, worldWith } from '../test/fixtures';

/**
 * Strips to look at without a server.
 *
 * The strip's hardest problems are visual — whether the active tab reads as
 * active beside its neighbours, whether the phase edge is legible at the
 * strip's scale, whether four tabs at the panel's 320px minimum truncate
 * usefully — and jsdom computes no layout, so tests cannot answer them.
 *
 * Every tab is read through `tabAttribution` by the component itself: the
 * fixture supplies a world and a tab list, never an attribution, so a story
 * cannot draw a tab the selector could not produce.
 */

/** A task, its agent and its plan: the trio one row of the strip is built from. */
function chain(id: string, title: string, agentId: string, command: string) {
  return {
    task: makeTask({ id, notebookId: id, title, command }),
    agent: makeAgent({ id: agentId, taskId: id }),
    document: makeDocument({ id: `doc-${agentId}`, agentId, taskId: id, title: 'Plan' }),
  };
}

const planning = chain('NORT-7', 'Add order export', 'a1b2c3d4', 'plan-task');
// An execute task runs no skill: no command is the ordinary build case.
const building = chain('NORT-9', 'Migrate to Postgres 16', 'd9a4c7f1', '');
const shaping = chain('MAEL-40', 'Tell a stopped agent from an idle one', 'f0a6f965', 'shape');
/** A document whose title runs past the label's measure. */
const longDocument = makeDocument({
  id: 'doc-long',
  agentId: shaping.agent.id,
  taskId: shaping.task.id,
  title: 'Migration runbook, third pass — collation rebuild and swap',
});
const landing = chain('NORT-3', 'Ship the export endpoint', 'b7e1c0a2', 'watch-pr');
/**
 * A dated id at full length, as the maelstrom notebook writes them. Short
 * `NORT-7` ids flatter the strip: this is the width the tab has to hold, and
 * it is why a session tab carries no word beside its id.
 */
const dated = chain(
  'maelstrom/2026-09-22.1',
  'Panel tabs: say which agent, and look it',
  '52ec960f',
  'plan-task',
);

/** An agent with no task: its own id fills the id slot, and it draws no phase. */
const free = makeAgent({ id: '3f8c1e90', taskId: '' });

export const world = worldWith({
  tasks: [planning.task, building.task, shaping.task, landing.task, dated.task],
  agents: [planning.agent, building.agent, shaping.agent, landing.agent, dated.agent, free],
  documents: [
    planning.document,
    building.document,
    shaping.document,
    landing.document,
    dated.document,
    longDocument,
  ],
});

export interface Strip {
  tabs: PanelTab[];
  activeTabKey: string;
}

/** The one-tab strip: nothing to rank against, so the active state stands alone. */
export const single: Strip = {
  tabs: [sessionTab(building.agent.id)],
  activeTabKey: `session:${building.agent.id}`,
};

/** Four phases side by side: where the edge is checked for legibility and hue. */
export const fourPhases: Strip = {
  tabs: [
    sessionTab(shaping.agent.id),
    sessionTab(planning.agent.id),
    sessionTab(building.agent.id),
    sessionTab(landing.agent.id),
  ],
  activeTabKey: `session:${building.agent.id}`,
};

/** The pair the naming rule exists for: a session and its own plan, read together. */
export const sessionAndPlan: Strip = {
  tabs: [sessionTab(building.agent.id), documentTab(building.document.id)],
  activeTabKey: `document:${building.document.id}`,
};

/** A free agent beside a task's: the id slot filled from the next source down. */
export const freeAgent: Strip = {
  tabs: [sessionTab(building.agent.id), sessionTab(free.id)],
  activeTabKey: `session:${free.id}`,
};

/** A tab whose entity has left the world: no phase, no title, still drawn. */
export const gone: Strip = {
  tabs: [sessionTab(building.agent.id), sessionTab('deadbeef')],
  activeTabKey: 'session:deadbeef',
};

/** A label past its measure: the id holds, the label takes the ellipsis. */
export const longTitle: Strip = {
  tabs: [sessionTab(shaping.agent.id), documentTab(longDocument.id)],
  activeTabKey: `document:${longDocument.id}`,
};

/**
 * Ids at the length the notebook really writes them, with a free agent beside
 * them. This is the strip the workbench got wrong while every id was six
 * characters — check that a session reads without a word, and that the plan
 * beside it still says which document it is.
 */
export const realWidths: Strip = {
  tabs: [sessionTab(dated.agent.id), documentTab(dated.document.id), sessionTab(free.id)],
  activeTabKey: `document:${dated.document.id}`,
};
