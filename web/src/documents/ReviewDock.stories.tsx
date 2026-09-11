import type { Story } from '@ladle/react';
import { QueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { App } from '../App';
import type { Document } from '../protocol/documents';
import type { PermissionRequestItem, PlanReviewItem } from '../protocol/transcript';
import { createFakeServer } from '../test/fakeServer';
import { makePermissionRequest, makePlanReview } from '../test/fixtures';
import { SEED_TIME, seedWorld } from '../test/seedWorld';

export default { title: 'Documents / Review dock' };

/**
 * The dock under a document, drawn by the real app on the seeded world.
 *
 * What these stories answer that the suite cannot: whether the dock is one row
 * or two, whether a control clears the thumb floor, and where the context sheet
 * lands. Check 390px and a wide panel, in both schemes. Below 840px the app
 * draws the narrow layout, so 390px is the phone. See `web/DESIGN.md`,
 * "Seeing a change".
 *
 * Approve must sit in the same place, drawn the same way, in all three stories.
 * Switching between them is how that is read.
 */

/** The two wait kinds the dock draws as a band. */
type DockWait = PlanReviewItem | PermissionRequestItem;

/** NORT-9's agent. Every wait here is one it raised. */
const AGENT = 'd9a4c7f1';

/** Stamped like the rest of the seed, so the transcript reads in one order. */
const WAIT_TS = SEED_TIME;

/**
 * A wait has to be in the transcript as well as on the agent row. `DocumentTab`
 * lights the dock from `pendingRequestIds`; `DecisionCard` reads the requests
 * from the agent detail route, which the fake server builds by searching the
 * transcript. Set only the agent row and the band draws lit and empty.
 */
function planReviewItem(): DockWait {
  return makePlanReview({
    id: 'nort9-plan-review',
    ts: WAIT_TS,
    requestId: 'req-nort9-plan',
    documentId: 'doc-nort9-plan',
  });
}

function permissionItem(): DockWait {
  return makePermissionRequest({
    id: 'nort9-permission',
    ts: WAIT_TS,
    requestId: 'req-nort9-write',
    input: { file_path: 'migrations/0042_collation.sql' },
    description: 'Write migrations/0042_collation.sql',
  });
}

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
  wait = planReviewItem,
}: {
  status?: Document['status'];
  /** The request the agent waits on, or null for the document's own route. */
  wait?: (() => DockWait) | null;
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
    // A request the agent still waits on. Without one the dock draws the
    // document's own review route instead, which is the contrast the Settled
    // story shows.
    if (wait) {
      const item = wait();
      // Fast Refresh re-runs this effect, and `append` does not replace by id.
      // A second copy would make `Before this · N` count one wait twice.
      const already = server.transcripts[AGENT]?.items.some((i) => i.id === item.id);
      if (!already) server.append(AGENT, item);
      server.change({ kind: 'agent', ids: [AGENT] }, (w) => {
        w.agents[AGENT] = {
          ...w.agents[AGENT]!,
          state: item.type === 'plan_review' ? 'awaiting-plan-review' : 'awaiting-permission',
          waitingOn: item.requestId,
          pendingRequestIds: [item.requestId],
        };
      });
    }
  }, [deps, status, wait]);

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
 * the bottom carrying the amber rule and wash, and the whole band is one row on
 * a wide panel — `Before this`, Approve, the reason field, Deny. Drag the panel
 * under 30rem and `Before this` takes a line of its own.
 */
export const AwaitingReview: Story = () => <Harness />;

/**
 * A permission on the same document. The band carries neither the
 * `Permission · Write` heading nor the tool input.
 *
 * What to look at: this band and the one above are the same shape.
 */
export const AwaitingPermission: Story = () => <Harness wait={permissionItem} />;

/**
 * The same document with no wait on it, so the document's own review route
 * takes the dock instead.
 *
 * What to look at: the two share a chassis. The rule goes back to the plain
 * hairline and the ground back to the raised tone, because nothing is asking.
 * Approve leads here too, drawn as the primary.
 */
export const Settled: Story = () => <Harness status="approved" wait={null} />;
