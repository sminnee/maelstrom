import { PLAN_TOOL, QUESTION_TOOL } from '../protocol/transcript';
import type { ToolCallItem } from '../protocol/transcript';

export type ToolCardKind = 'bash' | 'edit' | 'write' | 'read' | 'wait' | 'generic';

/** Which card draws a tool call. Keyed on the tool name the daemon reports. */
export function classifyToolCall(item: ToolCallItem): ToolCardKind {
  switch (item.tool) {
    case 'Bash':
      return 'bash';
    case 'Edit':
      return 'edit';
    case 'Write':
      return 'write';
    case 'Read':
      return 'read';
    // The wait item that follows renders the prompt in full, so the call draws nothing.
    case QUESTION_TOOL:
    case PLAN_TOOL:
      return 'wait';
    default:
      return 'generic';
  }
}

/** One line naming the call, for the card header. */
export function toolCallTitle(item: ToolCallItem): string {
  const input = item.input;
  const s = (v: unknown) => (typeof v === 'string' ? v : '');
  switch (classifyToolCall(item)) {
    case 'bash':
      return s(input.description) || s(input.command);
    case 'edit':
    case 'write':
    case 'read':
      return s(input.file_path);
    default:
      return s(input.url) || s(input.query) || s(input.description) || '';
  }
}
