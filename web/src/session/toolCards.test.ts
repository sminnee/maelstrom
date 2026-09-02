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

describe('classifyToolCall', () => {
  it.each([
    ['Bash', { command: 'ls' }, 'bash'],
    ['Edit', { file_path: 'a.py', old_string: 'a', new_string: 'b' }, 'edit'],
    ['Write', { file_path: 'a.py', content: 'x' }, 'write'],
    ['Read', { file_path: 'a.py' }, 'read'],
    ['WebFetch', { url: 'https://example.com' }, 'generic'],
    ['ExitPlanMode', {}, 'generic'],
  ])('%s → %s', (tool, input, kind) => {
    expect(classifyToolCall(call(tool, input))).toBe(kind);
  });
});
