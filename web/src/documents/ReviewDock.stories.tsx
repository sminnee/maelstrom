import type { Story } from '@ladle/react';
import { QueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { App } from '../App';
import type { Document } from '../protocol/documents';
import { createFakeServer } from '../test/fakeServer';
import { seedWorld } from '../test/seedWorld';

export default { title: 'Documents / Review dock' };

/**
 * The dock under a document, drawn by the real app on the seeded world.
 *
 * What these stories answer that the suite cannot: whether the dock is one row
 * or two, whether a control clears the thumb floor, and where the context sheet
 * lands. Check 390px and a wide panel, in both schemes. Below 840px the app
 * draws the narrow layout, so 390px is the phone. See `web/DESIGN.md`,
 * "Seeing a change".
 */

/** NORT-9's plan, at whichever status the story wants to look at. */
function planDocument(status: Document['status']): Document {
  return {
    id: 'doc-nort9-plan',
    agentId: 'd9a4c7f1',
    taskId: 'NORT-9',
    kind: 'plan',
    title: 'plan.md',
    markdown: [
      '# Migrate to Postgres 16',
      '',
      'The collation changes under the new major, so every index on a text',
      'column is rebuilt. The migration runs in three passes.',
      '',
      '## Passes',
      '',
      '1. Copy the schema and the non-text indexes.',
      '2. Stream the rows, oldest first, in batches of ten thousand.',
      '3. Rebuild the text indexes with the new collation and swap.',
      '',
      'Pass two is resumable: the batch cursor is committed with the batch, so',
      'a restart picks up where it stopped rather than starting again.',
      '',
      '## Rollback',
      '',
      'The old cluster stays up and read-only until the swap is verified.',
    ].join('\n'),
    version: 1,
    status,
    source: { type: 'plan_review', requestId: 'req-nort9-plan', planFilePath: '' },
  };
}

/**
 * The app on a fake server, opened at the plan document.
 *
 * `deps` is the same injection point `renderApp` uses, so a story runs the
 * production tree rather than a stand-in that can drift from it.
 */
function Harness({
  status = 'awaiting-review',
  waiting = true,
}: {
  status?: Document['status'];
  waiting?: boolean;
}) {
  const [deps] = useState(() => {
    const seed = seedWorld();
    const server = createFakeServer({ world: seed.world, transcripts: seed.transcripts });
    return {
      deps: {
        api: server.api,
        eventSourceFactory: server.eventSourceFactory,
        webSocketFactory: server.webSocketFactory,
        streamReconnectMs: 10,
        queryClient: new QueryClient({
          defaultOptions: { queries: { retry: false, staleTime: Infinity } },
        }),
      },
      server,
    };
  });

  useEffect(() => {
    const { server } = deps;
    const doc = planDocument(status);
    server.change({ kind: 'document', ids: [doc.id] }, (w) => {
      w.documents[doc.id] = doc;
    });
    // A plan review the agent still waits on. Without the wait the dock draws
    // the document's own review route instead, which is the contrast the
    // Settled story shows.
    if (waiting) {
      server.change({ kind: 'agent', ids: ['d9a4c7f1'] }, (w) => {
        w.agents['d9a4c7f1'] = {
          ...w.agents['d9a4c7f1']!,
          state: 'awaiting-plan-review',
          pendingRequestIds: ['req-nort9-plan'],
        };
      });
    }
  }, [deps, status, waiting]);

  return (
    <div style={{ height: '100vh' }}>
      <App deps={deps.deps} />
    </div>
  );
}

/**
 * A plan awaiting the agent's own review. Open NORT-9 and follow `plan.md`.
 *
 * What to look at: the plan reads from its first line, the dock is one band at
 * the bottom carrying the amber rule and wash, and `Before this` is a control
 * rather than a band of its own.
 */
export const AwaitingReview: Story = () => <Harness />;

/**
 * The same document with no wait on it, so the document's own review route
 * takes the dock instead.
 *
 * What to look at: the two share a chassis. The rule goes back to the plain
 * hairline and the ground back to the raised tone, because nothing is asking.
 */
export const Settled: Story = () => <Harness status="approved" waiting={false} />;
