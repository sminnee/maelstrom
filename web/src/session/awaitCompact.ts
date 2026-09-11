import type { AgentId } from '../protocol/ids';
import type { TranscriptItem } from '../protocol/transcript';
import { useAppStore } from '../store/store';

/**
 * How long to hold the button before giving up on a boundary that never came.
 *
 * A real compact takes 10s–130s. This is not a deadline for the work — it is
 * the point past which a spinner is lying rather than waiting, so it is set
 * far above any compact that is actually going to finish.
 */
export const BACKSTOP_MS = 5 * 60 * 1000;

/** Every item the transcript holds for `agentId` right now. */
function itemsOf(agentId: AgentId): TranscriptItem[] {
  return useAppStore.getState().transcripts[agentId]?.items ?? [];
}

/**
 * Settles when `agentId` finishes compacting, and rejects when it does not.
 *
 * The Compact button is busy for exactly as long as this is unsettled, so what
 * settles it decides what the spinner means. Four things can:
 *
 * - A `compact` item arrives. This is the only event that says a compact
 *   finished; see `CONTEXT.md`, "Compact boundary".
 * - The turn ends without one. That is the refusal path: a refusal is shaped
 *   exactly like a success, so the missing boundary is the only signal. See
 *   `docs/dev/agent-daemon.md`, "A compact". A real compact emits its boundary
 *   before the turn's result, so this never races the success it rules out.
 * - The agent exits. It appends no transcript item when it goes, so without
 *   this the button would spin out the whole backstop over a dead agent.
 * - The backstop expires. The button reports an error rather than spinning
 *   for ever.
 *
 * `registerAbandon` is handed a function that ends the wait with an error. The
 * world lives in the query cache, which writes nothing to this store, so a
 * subscription here cannot see an exit at all — the caller renders from the
 * world, so it is the one that can say the agent has gone.
 *
 * The items already on the transcript are remembered by id, not by count: a
 * transcript is replaced wholesale by a re-snapshot, dropped when the last
 * view releases it, and truncated by the host's own ring. Any of those
 * renumbers a positional marker, and an index taken before them would scan
 * the wrong window for ever.
 */
export function awaitCompact(
  agentId: AgentId,
  registerAbandon: (abandon: (reason: string) => void) => void,
  backstopMs: number = BACKSTOP_MS,
): Promise<void> {
  return new Promise((resolve, reject) => {
    // Taken here rather than at the click: the relay's round trip lands the
    // echoed user turn, and a `turn_result` from the turn before it, inside a
    // window opened any earlier — which would read as a refusal.
    const already = new Set(itemsOf(agentId).map((i) => i.id));
    let done = false;
    const finish = (err?: Error) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      unsubscribe();
      if (err) reject(err);
      else resolve();
    };

    const timer = setTimeout(
      () => finish(new Error('The compact did not finish in five minutes.')),
      backstopMs,
    );

    let last: TranscriptItem[] | null = null;
    const check = () => {
      const items = itemsOf(agentId);
      // The store has no selector middleware, so this runs on every write —
      // a tab switch, a filter, another agent's turn. Comparing the array
      // reference keeps all of those off the scan below.
      if (items === last) return;
      last = items;
      const since = items.filter((i) => !already.has(i.id));
      if (since.some((i) => i.type === 'compact')) return finish();
      // The transcript is the one place both of these land, so the wait reads
      // them from it rather than joining a second source that could disagree
      // about the order.
      if (since.some((i) => i.type === 'turn_result')) {
        finish(new Error('The agent did not compact. The conversation may be too short.'));
      }
    };

    registerAbandon((reason: string) => finish(new Error(reason)));
    const unsubscribe = useAppStore.subscribe(check);
    check();
  });
}
