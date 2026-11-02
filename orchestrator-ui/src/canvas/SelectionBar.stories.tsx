import type { Story } from '@ladle/react';
import { useEffect, useState } from 'react';
import { createMemoryRouter } from 'react-router';
import { FakeApp } from '../fake/FakeApp';
import { routes } from '../nav/routes';

export default { title: 'Canvas / Bulk bar' };

/**
 * The real app on the comms scenario, with `select` Shift-clicked once the
 * board has drawn. The Selection is view state, so a click is the only way in.
 */
function Harness({ select }: { select: string[] }) {
  // A memory router: Ladle owns the page's URL.
  const [router] = useState(() => createMemoryRouter(routes, { initialEntries: ['/desk'] }));
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!document.querySelector('[data-testid="task-node"]')) return;
      window.clearInterval(timer);
      for (const id of select) {
        document
          .querySelector(`[data-task-id="${id}"]`)
          ?.dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: true }));
      }
    }, 50);
    return () => window.clearInterval(timer);
  }, [select]);
  return (
    <div style={{ height: '100vh' }}>
      <FakeApp scenario="comms" router={router} />
    </div>
  );
}

/**
 * What to look at: three nodes drawn with the dashed Selected outline, and the
 * bar floating top-centre over the board. NORT-12 feeds `c2`, so Unlink offers
 * it. NORT-7 has a live agent, so Dismiss asks first.
 */
export const ThreeSelected: Story = () => <Harness select={['NORT-12', 'NORT-7', 'f2c6a9d4']} />;
