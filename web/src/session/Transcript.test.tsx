import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { Transcript } from './Transcript';
import { replayFixture } from '../test/replayFixture';

describe('Transcript', () => {
  it('renders one card per item of a replayed fixture, in order and typed by item', () => {
    const state = replayFixture('plan-review.jsonl');
    const items = state.transcripts['ag1']!.items;
    render(<Transcript items={items} truncatedBefore={false} />);
    const kinds = screen
      .getAllByTestId('transcript-card')
      .map((c) => c.getAttribute('data-item-type'));
    expect(kinds).toEqual(items.map((i) => i.type));
    expect(kinds).toEqual(
      expect.arrayContaining(['tool_call', 'plan_review', 'permission_request', 'turn_result']),
    );
  });

  it('shows a Bash command with its output and a Write as its content', () => {
    const state = replayFixture('plan-review.jsonl');
    render(<Transcript items={state.transcripts['ag1']!.items} truncatedBefore={false} />);
    const cards = screen.getAllByTestId('transcript-card');
    const bash = cards.find((c) => c.querySelector('[data-tool-kind="bash"]'))!;
    expect(
      within(bash).getByText(/\$ ls -ld \/Users\/sminnee\/.claude\/plans/),
    ).toBeInTheDocument();
    const failedWrite = cards.find(
      (c) => c.querySelector('[data-tool-kind="write"][data-status="error"]') !== null,
    )!;
    expect(within(failedWrite).getByText(/EPERM: operation not permitted/)).toBeInTheDocument();
    const write = cards.find((c) =>
      c.querySelector('[data-tool-kind="write"][data-status="done"]'),
    )!;
    expect(within(write).getByText('hi')).toBeInTheDocument();
  });

  it('shows an Edit as diff rows', () => {
    render(
      <Transcript
        truncatedBefore={false}
        items={[
          {
            id: 'e1',
            ts: '',
            type: 'tool_call',
            toolUseId: 'e1',
            tool: 'Edit',
            input: { file_path: 'a.py', old_string: 'x = 1\n', new_string: 'x = 2\n' },
            status: 'done',
          },
        ]}
      />,
    );
    const rows = screen.getAllByTestId('diff-row');
    expect(rows.map((r) => r.getAttribute('data-kind'))).toEqual(['remove', 'add']);
  });

  it('a denied permission shows its decision', () => {
    const state = replayFixture('permission-denied.jsonl');
    render(<Transcript items={state.transcripts['ag1']!.items} truncatedBefore={false} />);
    const card = screen
      .getAllByTestId('transcript-card')
      .find((c) => c.querySelector('[data-tool-kind="bash"]'))!;
    expect(within(card).getByText('denied')).toBeInTheDocument();
  });
});
