import type { RequestId } from '../protocol/ids';
import { PLAN_TOOL, QUESTION_TOOL } from '../protocol/normalise';
import type { MessageItem, ToolCallItem, TranscriptItem } from '../protocol/transcript';

export type ContextItem = MessageItem | ToolCallItem;

/**
 * The last `n` things the agent said or did before it raised `requestId`:
 * assistant messages and tool calls, in order. The tool call that raised the
 * request is skipped: the prompt shows it in full. Empty when no item carries
 * the request.
 */
export function contextBefore(items: TranscriptItem[], requestId: RequestId, n = 3): ContextItem[] {
  const at = items.findIndex((i) => 'requestId' in i && i.requestId === requestId);
  if (at === -1) return [];
  const wait = items[at]!;
  let end = at;
  const previous = items[end - 1];
  if (previous?.type === 'tool_call' && previous.tool === raisingTool(wait)) end -= 1;
  const context: ContextItem[] = [];
  for (let i = end - 1; i >= 0 && context.length < n; i -= 1) {
    const item = items[i]!;
    if (item.type === 'tool_call' || (item.type === 'message' && item.role === 'assistant')) {
      context.unshift(item);
    }
  }
  return context;
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

/** The tool whose `tool_use` raised this wait, or '' for an item that is not a wait. */
function raisingTool(item: TranscriptItem): string {
  switch (item.type) {
    case 'question':
      return QUESTION_TOOL;
    case 'plan_review':
      return PLAN_TOOL;
    case 'permission_request':
      return item.tool;
    default:
      return '';
  }
}
