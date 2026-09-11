/**
 * How full an agent's context is, in the one short unit a header holds.
 *
 * The same rounding as `_tokens` in `src/maelstrom/agent_view.py`, which writes
 * the TUI footer, so one agent's size reads the same way on both surfaces. The
 * quantity differs: the footer still reports the session's cumulative total.
 * Rounds down, so "148k ctx" means at least 148,000.
 */

const THOUSAND = 1_000;
const MILLION = 1_000_000;

/**
 * What the agent's prompt last held, or `''` when there is nothing to report.
 *
 * A level, not a total, so it falls when the agent compacts. This is the figure
 * a reader deciding whether to compact wants — see `docs/dev/agent-daemon.md`,
 * "A turn". A context with nothing to say says nothing rather than "0": a
 * header field that is empty drops out, and a fresh agent holds no prompt.
 */
export function contextSize(tokens: number): string {
  if (!Number.isFinite(tokens) || tokens <= 0) return '';
  // Truncated to a tenth, not rounded: `toFixed` would round 1,299,999 up to
  // 1.3M and read larger than the context is.
  if (tokens >= MILLION) return `${(Math.floor(tokens / 100_000) / 10).toFixed(1)}M ctx`;
  if (tokens >= THOUSAND) return `${Math.floor(tokens / THOUSAND)}k ctx`;
  return `${Math.floor(tokens)} ctx`;
}
