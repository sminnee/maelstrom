import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { Transcript } from './Transcript';
import { classifyToolCall } from './toolCards';
import { makePlanReview } from '../test/fixtures';
import { goldenItems } from '../test/goldens';
import type { TranscriptItem } from '../protocol/transcript';

const raisesAWait = (item: TranscriptItem) =>
  item.type === 'tool_call' && classifyToolCall(item) === 'wait';

describe('Transcript', () => {
  it('renders one card per item of a normalised fixture, in order and typed by item', () => {
    const items = goldenItems('plan-review.jsonl');
    render(<Transcript items={items} truncatedBefore={false} />);
    const kinds = screen
      .getAllByTestId('transcript-card')
      .map((c) => c.getAttribute('data-item-type'));
    // The call that raises a wait draws nothing, so it takes no row of its own.
    expect(kinds).toEqual(items.filter((i) => !raisesAWait(i)).map((i) => i.type));
    expect(kinds).toEqual(
      expect.arrayContaining(['tool_call', 'plan_review', 'permission_request', 'turn_result']),
    );
  });

  it('leaves no empty row where the call that raised a wait would have drawn', () => {
    const items = goldenItems('plan-review.jsonl');
    expect(items.some(raisesAWait)).toBe(true);
    render(<Transcript items={items} truncatedBefore={false} />);
    // An empty wrapper still takes a gap slot, so the hole is as visible as the dump was.
    for (const card of screen.getAllByTestId('transcript-card')) {
      expect(card).not.toBeEmptyDOMElement();
    }
  });

  it('shows a Bash command with its output and a Write as its content', () => {
    render(<Transcript items={goldenItems('plan-review.jsonl')} truncatedBefore={false} />);
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

  it('a plan review nothing answered no longer claims to await review', () => {
    render(<Transcript truncatedBefore={false} items={[makePlanReview({ stale: true })]} />);
    expect(screen.getByText('no longer pending')).toBeInTheDocument();
    expect(screen.queryByText('awaiting review')).toBeNull();
  });

  it('a gap says how many events the host dropped there', () => {
    render(
      <Transcript
        truncatedBefore={false}
        items={[{ id: 'g1', ts: '', type: 'gap', droppedEvents: 12 }]}
      />,
    );
    expect(screen.getByTestId('gap')).toHaveTextContent('12 earlier events were dropped here.');
  });

  it('a skill body is folded away behind the skill name', () => {
    render(
      <Transcript
        truncatedBefore={false}
        items={[
          {
            id: 's1',
            ts: '',
            type: 'skill',
            skill: 'mael',
            markdown: '# Skill heading\n\nThe conventions this file carries.',
          },
        ]}
      />,
    );
    const card = screen.getByTestId('skill');
    expect(card).not.toHaveAttribute('open');
    expect(within(card).getByText('mael')).toBeInTheDocument();
    expect(card).toHaveTextContent('The conventions this file carries.');
  });

  it('a denied permission shows its decision', () => {
    render(<Transcript items={goldenItems('permission-denied.jsonl')} truncatedBefore={false} />);
    const card = screen
      .getAllByTestId('transcript-card')
      .find((c) => c.querySelector('[data-tool-kind="bash"]'))!;
    expect(within(card).getByText('denied')).toBeInTheDocument();
  });
});

describe('tool cards', () => {
  it('start closed, whatever the tool, and one opens on its summary', () => {
    const items = goldenItems('subagent-turn.jsonl');
    render(<Transcript items={items} truncatedBefore={false} />);
    const cards = document.querySelectorAll('details');
    expect(cards.length).toBeGreaterThan(0);
    for (const card of cards) expect(card).not.toHaveAttribute('open');
    const first = cards[0]!;
    fireEvent.click(within(first).getByText('Agent'));
    expect(first).toHaveAttribute('open');
    expect(within(first).getByText(/"subagent_type"/)).toBeInTheDocument();
  });
});
