/**
 * How large a session has grown, in the one short unit a header holds.
 *
 * The same rule as `_tokens` in `src/maelstrom/agent_view.py`, which writes the
 * TUI footer: the two surfaces report one agent, so they say one thing. Rounds
 * down, so "148k tok" means at least 148,000.
 */

const THOUSAND = 1_000;
const MILLION = 1_000_000;

/**
 * `tokens` as a size, or `''` when there is nothing to report.
 *
 * A session that has spent nothing says nothing rather than "0 tok": a header
 * field that is empty drops out, and a fresh agent has no size worth a word.
 */
export function sessionSize(tokens: number): string {
  if (!Number.isFinite(tokens) || tokens <= 0) return '';
  // Truncated to a tenth, not rounded: `toFixed` would round 1,299,999 up to
  // 1.3M and read larger than the session is.
  if (tokens >= MILLION) return `${(Math.floor(tokens / 100_000) / 10).toFixed(1)}M tok`;
  if (tokens >= THOUSAND) return `${Math.floor(tokens / THOUSAND)}k tok`;
  return `${Math.floor(tokens)} tok`;
}
