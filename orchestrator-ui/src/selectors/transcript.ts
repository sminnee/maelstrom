import type { RequestId } from '../protocol/ids';
import type { MessageItem, TranscriptItem } from '../protocol/transcript';

/**
 * The last three assistant messages, in order, of any rank. No tool calls.
 * No partial message: only the session tab draws one.
 *
 * `before` names a wait, and the list then stops at it. Empty when no item
 * carries that request.
 */
export function recentMessages(
  items: TranscriptItem[],
  { before }: { before?: RequestId } = {},
): MessageItem[] {
  const end =
    before === undefined
      ? items.length
      : items.findIndex((i) => 'requestId' in i && i.requestId === before);
  return items
    .slice(0, Math.max(end, 0))
    .filter((i): i is MessageItem => i.type === 'message' && i.role === 'assistant' && !i.partial)
    .slice(-3);
}

/**
 * Whether the expanded card answers this wait, so the panel must not.
 * One request has one live prompt — see `docs/dev/orchestrator-ui.md`.
 *
 * Both ids are node ids: a task node draws under its task id, a free agent
 * under its own, so an agent with no task still matches its card.
 */
export function answeredOnCanvas(expandedNodeId: string | null, waitingNodeId: string): boolean {
  return expandedNodeId !== null && expandedNodeId === waitingNodeId;
}
