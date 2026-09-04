import { describe, expect, it } from 'vitest';
import { classifyToolCall } from './toolCards';
import type { ToolCallItem } from '../protocol/transcript';

const call = (tool: string, input: Record<string, unknown> = {}): ToolCallItem => ({
  id: 't',
  ts: '',
  type: 'tool_call',
  toolUseId: 't',
  tool,
  input,
  status: 'done',
});

describe('which card draws a tool call', () => {
  it.each([
    ['Bash', { command: 'ls' }, 'bash'],
    ['Edit', { file_path: 'a.py', old_string: 'a', new_string: 'b' }, 'edit'],
    ['Write', { file_path: 'a.py', content: 'x' }, 'write'],
    ['Read', { file_path: 'a.py' }, 'read'],
  ])('%s draws its own card → %s', (tool, input, kind) => {
    expect(classifyToolCall(call(tool, input))).toBe(kind);
  });

  it('a tool with no card of its own falls back to generic', () => {
    expect(classifyToolCall(call('WebFetch', { url: 'https://example.com' }))).toBe('generic');
  });

  it.each([['ExitPlanMode'], ['AskUserQuestion']])(
    '%s raises a wait the transcript renders itself, so it draws nothing',
    (tool) => {
      expect(classifyToolCall(call(tool, {}))).toBe('wait');
    },
  );
});
