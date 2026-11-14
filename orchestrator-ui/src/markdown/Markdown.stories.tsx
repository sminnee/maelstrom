import type { Story } from '@ladle/react';
import docStyles from '../documents/DocumentTab.module.css';
import { Markdown } from './Markdown';
import { planDocument } from './plan.fixture';

export default { title: 'Markdown' };

/**
 * The case the surface is tuned for. Read it at length rather than glancing at
 * it: the rhythm is what this story shows, and a screenshot of the first
 * viewport does not show whether a document holds up over a thousand lines.
 *
 * Every block here should advance a whole number of 24px rows. Two do not, by
 * design — the four-item list, which ends a half row out, and the table. To
 * check:
 *
 *     [...document.querySelector('[class*=markdown]').children].map(el => {
 *       const s = getComputedStyle(el);
 *       const h = el.getBoundingClientRect().height
 *         + parseFloat(s.marginTop) + parseFloat(s.marginBottom);
 *       return [el.tagName, h, h % 24];
 *     })
 */
export const PlanDocument: Story = () => (
  <div style={{ maxWidth: 'var(--measure-prose)', margin: '0 auto', padding: 24 }}>
    <Markdown source={planDocument} />
  </div>
);

const wideTable = `The commands each surface can run today.

| Feature | Action | Today | Backend support |
| --- | --- | --- | --- |
| Files | File existing | Yes | \`DocumentStore.read_file(worktree, path)\` |
| Tasks | Close task | No | \`mael task status done --task-id <id>\` via \`run_cmd_async\` |
| Agents | Resume a stopped agent | Partly | \`AgentDaemon.resume(session_id, spec=AgentSpec(...))\` |
`;

const narrowTable = `| Key | Value |
| --- | --- |
| a | 1 |
`;

/**
 * A surface that does not opt in. The narrow table keeps the column's left
 * edge. The wide table is wider than its 480px column: each column should keep
 * its longest word, and the table should scroll sideways while the paragraph
 * keeps its width.
 */
export const WideTable: Story = () => (
  <div style={{ maxWidth: 480, margin: '0 auto', padding: 24 }}>
    <Markdown source={[narrowTable, wideTable].join('\n')} />
  </div>
);

const widerTable = `A table wider than the container scrolls inside it.

| Column one | Column two | Column three | Column four | Column five | Column six |
| --- | --- | --- | --- | --- | --- |
| \`AgentDaemon.resume(session_id, spec=AgentSpec(...))\` | \`DocumentStore.read_file(worktree, path)\` | \`mael task status done --task-id <id>\` | \`run_cmd_async(cmd, cwd=worktree)\` | \`OrchestratorClient.get_world()\` | \`TaskHomes.resolve(project, task_id)\` |
`;

const nestedTable = `A table in a list item stays in the column.

- An item with a table:

  | Feature | Action | Today | Backend support |
  | --- | --- | --- | --- |
  | Agents | Resume a stopped agent | Partly | \`AgentDaemon.resume(session_id, spec=AgentSpec(...))\` |
`;

/**
 * The Document tab's own \`.body\` and \`.prose\`, 900px wide. The narrow table
 * keeps the column's left edge. The second table grows past the column,
 * centred on it. The third is wider than \`.body\` and scrolls. The table in
 * the list item stays in the column.
 */
export const WideTableBreakout: Story = () => (
  <div
    style={{
      display: 'flex',
      maxWidth: 900,
      margin: '0 auto',
      outline: '1px dashed var(--border)',
    }}
  >
    <div className={docStyles.body}>
      <Markdown
        className={docStyles.prose}
        source={[narrowTable, wideTable, widerTable, nestedTable].join('\n')}
      />
    </div>
  </div>
);
