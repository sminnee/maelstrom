import type { Story } from '@ladle/react';
import type { Document } from '../protocol/documents';
import type { PermissionRequestItem, PlanReviewItem } from '../protocol/transcript';
import { FakeApp } from '../fake/FakeApp';
import { makePermissionRequest, makePlanReview } from '../fake/fixtures';
import { SEED_TIME, type Seed } from '../fake/seedWorld';

export default { title: 'Documents / Review dock' };

/**
 * The dock under a document, drawn by the real app on the seeded world.
 *
 * What these stories answer that the suite cannot: whether the dock is one row
 * or two, whether a control clears the thumb floor, and where the context sheet
 * lands. Check 390px and a wide panel, in both schemes. Below 840px the app
 * draws the narrow layout, so 390px is the phone. See `orchestrator-ui/DESIGN.md`,
 * "Seeing a change".
 *
 * Approve must sit in the same place, drawn the same way, in every story.
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
    title: 'Plan',
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
    group: { id: 'doc-nort9-plan', title: 'Plan', position: 0 },
  };
}

/** A task id of real length. `NORT-9` flatters the header line. */
const GROUP_TASK = 'askastro/daily.maintenance.2026-10-02';

/**
 * A member of a task set is titled by its filename, so a title is one token
 * with no space to break at.
 */
const GROUP_FILES = [
  '.drafts/rebuild-nightly-ephemeris-cache.md',
  '.drafts/rotate-observatory-api-credentials.md',
  '.drafts/prune-stale-session-transcripts.md',
  '.drafts/rerun-failed-horoscope-renders.md',
];

/** A body longer than one screen, with a long `code` token and a long `pre` line. */
const GROUP_BODY = [
  '# Rebuild the nightly ephemeris cache',
  '',
  'The cache is built from the upstream feed once a night. A partial feed leaves',
  'a partial cache, and the readers do not know the difference.',
  '',
  '## Steps',
  '',
  '1. Fetch the feed and check its row count against the manifest.',
  '2. Write the new cache to `var/cache/ephemeris/2026-10-02T00-00-00Z/planetary_positions_by_julian_day.partial.sqlite3`.',
  '3. Swap the symlink only when the row count matches.',
  '4. Delete every cache but the last three.',
  '',
  '```sh',
  'uv run astro cache rebuild --feed https://feeds.example.org/ephemeris/v4/daily.json --manifest var/manifests/2026-10-02.json --keep 3',
  '```',
  '',
  ...Array.from({ length: 12 }, (_, i) => [
    `## Check ${i + 1}`,
    '',
    'The reader opens the cache read-only and compares a sample of rows with the',
    'feed. A mismatch stops the swap and leaves the old cache in place.',
    '',
  ]).flat(),
  '## Seams under test',
  '',
  'The cache directory. One fixture per feed shape, asserted through the reader.',
].join('\n');

/** A task set of four, as a planner presents it: one tag, one verdict. */
function taskGroup(): Document[] {
  return GROUP_FILES.map((filename, position) => ({
    id: `doc-group-${position}`,
    agentId: AGENT,
    taskId: GROUP_TASK,
    kind: 'tasks',
    title: filename,
    markdown: GROUP_BODY,
    version: 1,
    status: 'awaiting-review',
    source: { type: 'draft_file', fileId: null, filename },
    group: { id: 'doc-group-0', title: 'Daily maintenance', position },
  }));
}

/**
 * The app on a fake server, with the story's documents seeded.
 *
 * Everything is written to the seed before the first fetch. A change notice
 * sent before the event stream opens reaches nobody.
 */
function Harness({
  status = 'awaiting-review',
  wait = planReviewItem,
  documents,
}: {
  status?: Document['status'];
  /** The documents to seed. The plan document at `status` when left out. */
  documents?: () => Document[];
  /** The request the agent waits on, or null for the document's own route. */
  wait?: (() => DockWait) | null;
}) {
  const amend = (seed: Seed) => {
    for (const doc of documents ? documents() : [planDocument(status)]) {
      seed.world.documents[doc.id] = doc;
      // The header draws a phase and a task title only for a task it can find.
      seed.world.tasks[doc.taskId] ??= {
        ...seed.world.tasks['NORT-7']!,
        id: doc.taskId,
        title: 'Plan the daily maintenance run',
      };
    }
    // A request the agent still waits on. Without one the dock draws the
    // document's own review route instead, which is the contrast the Settled
    // story shows.
    if (!wait) return;
    const item = wait();
    seed.transcripts[AGENT]!.items.push(item);
    seed.world.agents[AGENT] = {
      ...seed.world.agents[AGENT]!,
      state: item.type === 'plan_review' ? 'awaiting-plan-review' : 'awaiting-permission',
      waitingOn: item.requestId,
      pendingRequestIds: [item.requestId],
    };
  };
  return (
    <div style={{ height: '100vh' }}>
      <FakeApp amend={amend} />
    </div>
  );
}

/**
 * A plan awaiting the agent's own review. Open NORT-9 and follow `Plan`.
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

/**
 * A task set of four awaiting review, with no wait on the agent. Open NORT-9
 * and follow the first document under `Daily maintenance`.
 *
 * What to look at, at 390px: the dock wraps to more than one row, and the body
 * still ends above it. Nothing is wider than the screen — not the header line,
 * the sibling links, the code block or the summary field. Scroll to the end and
 * the last paragraph clears the dock.
 */
export const AwaitingGroupReview: Story = () => <Harness wait={null} documents={taskGroup} />;
