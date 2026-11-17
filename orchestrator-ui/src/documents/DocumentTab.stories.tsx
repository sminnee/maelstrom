import type { Story } from '@ladle/react';
import { useState } from 'react';
import { createMemoryRouter } from 'react-router';
import type { Document } from '../protocol/documents';
import { FakeApp } from '../fake/FakeApp';
import type { Seed } from '../fake/seedWorld';
import { routes } from '../nav/routes';

export default { title: 'Documents / Versions and links' };

/**
 * A stored plan with three versions and links from other tasks, drawn by the
 * real app on the seeded world. The tab opens at the address the story names.
 *
 * What these stories answer that the suite cannot: whether the version picker
 * sits in the header line without pushing the status off it, how quiet the
 * earlier-version note reads, and whether "Referenced by" reads as a part of
 * the document rather than of the prose. Check 390px and a wide panel, in both
 * schemes.
 */

const BODY = (passes: number) =>
  [
    '# Migrate to Postgres 16',
    '',
    'The collation changes under the new major, so every index on a text',
    'column is rebuilt.',
    '',
    '## Set up',
    '',
    'Take a snapshot of the primary and start a replica on the new major.',
    '',
    '## Passes',
    '',
    ...Array.from({ length: passes }, (_, i) => `${i + 1}. Pass ${i + 1}.`),
    '',
    ...(passes > 2 ? ['## Rollback', '', 'The old cluster stays up until the swap.'] : []),
  ].join('\n');

const LATEST: Document = {
  id: 'stored-nort9-plan',
  agentId: 'd9a4c7f1',
  taskId: 'NORT-9',
  kind: 'plan',
  title: 'Migrate to Postgres 16',
  markdown: BODY(3),
  version: 3,
  status: 'approved',
  source: { type: 'stored', name: 'plan' },
  group: { id: 'stored-nort9-plan', title: 'Migrate to Postgres 16', position: 0 },
  archived: false,
  links: [
    { taskId: 'NORT-7', fragment: '' },
    { taskId: 'NORT-12', fragment: 'set-up' },
    { taskId: 'NORT-7', fragment: 'rollback' },
  ],
};

function Harness({ at }: { at: string }) {
  const [router] = useState(() =>
    createMemoryRouter(routes, {
      initialEntries: [`/desk/graph?panel=document/${encodeURIComponent(at)}`],
    }),
  );
  const amend = (seed: Seed) => {
    seed.world.documents[LATEST.id] = LATEST;
    seed.world.earlierVersions[LATEST.id] = [
      { ...LATEST, markdown: BODY(1), version: 1, status: 'changes-requested' },
      { ...LATEST, markdown: BODY(2), version: 2, status: 'changes-requested' },
    ];
  };
  return (
    <div style={{ height: '100vh' }}>
      <FakeApp amend={amend} router={router} />
    </div>
  );
}

/**
 * The latest version. What to look at: the picker reads `v3 · Approved · …`
 * in the header line. Scroll to the end: "Referenced by" lists the whole
 * document first, then `#set-up`, then `#rollback`, each row with Unlink, and
 * the Link a task form under them.
 */
export const Latest: Story = () => <Harness at={LATEST.id} />;

/**
 * Version 1, opened at a heading it does not have. What to look at: the two
 * notes under the header are quiet, one line each, and the dock is gone.
 */
export const EarlierVersion: Story = () => <Harness at={`${LATEST.id}@v1#rollback`} />;

/** Opened at `#rollback`: the body scrolls to the heading. */
export const AtAFragment: Story = () => <Harness at={`${LATEST.id}#rollback`} />;
