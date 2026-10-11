import { describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import type { DiffRowData } from './DiffRow';
import type * as HighlightModule from './highlight';
import { useRowTokens } from './useRowTokens';

/** Each line as one plain token, counted. */
const highlighted = vi.hoisted(() => ({ calls: 0 }));
vi.mock('./highlight', async (importOriginal) => {
  const actual = await importOriginal<typeof HighlightModule>();
  return {
    ...actual,
    highlightLines: (lines: string[]) => {
      highlighted.calls += 1;
      return Promise.resolve(lines.map((content) => [{ content }]));
    },
  };
});

// Each test has its own text: the token cache outlives a test.
const hunk = (text: string): DiffRowData[] => [
  { kind: 'context', text: `const ${text} = 1;` },
  { kind: 'add', text: `const ${text}2 = 2;` },
];

describe('useRowTokens', () => {
  it('highlights nothing while disabled, and highlights once enabled', async () => {
    const rows = hunk('offscreen');
    const before = highlighted.calls;
    const { result, rerender } = renderHook(({ enabled }) => useRowTokens(rows, 'a.ts', enabled), {
      initialProps: { enabled: false },
    });
    await Promise.resolve();
    expect(highlighted.calls).toBe(before);
    expect(result.current).toBeNull();

    rerender({ enabled: true });
    await waitFor(() =>
      expect(result.current).toEqual([
        [{ content: 'const offscreen = 1;' }],
        [{ content: 'const offscreen2 = 2;' }],
      ]),
    );
  });

  it('gives tokens it already made at once, to new rows with the same text', async () => {
    const first = renderHook(() => useRowTokens(hunk('seen'), 'a.ts'));
    await waitFor(() => expect(first.result.current).not.toBeNull());
    const calls = highlighted.calls;

    const second = renderHook(() => useRowTokens(hunk('seen'), 'a.ts'));
    expect(second.result.current).toEqual(first.result.current);
    expect(highlighted.calls).toBe(calls);
  });

  it('highlights other text again', async () => {
    const first = renderHook(() => useRowTokens(hunk('one'), 'a.ts'));
    await waitFor(() => expect(first.result.current).not.toBeNull());
    const calls = highlighted.calls;

    const second = renderHook(() => useRowTokens(hunk('two'), 'a.ts'));
    await waitFor(() => expect(second.result.current).not.toBeNull());
    expect(highlighted.calls).toBeGreaterThan(calls);
  });
});
