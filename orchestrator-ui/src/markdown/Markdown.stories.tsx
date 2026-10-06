import type { Story } from '@ladle/react';
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

/**
 * A table wider than its 480px column. Each column should keep its longest
 * word, and the table should scroll sideways while the paragraph keeps its width.
 */
export const WideTable: Story = () => (
  <div style={{ maxWidth: 480, margin: '0 auto', padding: 24 }}>
    <Markdown source={wideTable} />
  </div>
);
