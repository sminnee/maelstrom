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
