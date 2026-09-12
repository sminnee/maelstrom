import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { Transcript } from './Transcript';
import { classifyToolCall } from './toolCards';
import { makePlanReview } from '../test/fixtures';
import { goldenItems } from '../test/goldens';
import type { TranscriptItem } from '../protocol/transcript';

const raisesAWait = (item: TranscriptItem) =>
  item.type === 'tool_call' && classifyToolCall(item) === 'wait';

/** One assistant message, whose id is also its body, so a row names itself. */
const said = (id: string, ts: string): TranscriptItem => ({
  id,
  ts,
  type: 'message',
  role: 'assistant',
  markdown: id,
});

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

  it('ends a turn with how it went and how long, and leaves the money to the header', () => {
    // `costUsd` on a turn is the session's total, not the turn's. The header
    // says it once; see `docs/dev/orchestrator-ui.md`.
    const items = goldenItems('normal-turn.jsonl');
    render(<Transcript items={items} truncatedBefore={false} />);
    const [line] = screen
      .getAllByTestId('transcript-card')
      .filter((c) => c.getAttribute('data-item-type') === 'turn_result');
    expect(line).toHaveTextContent('turn success · 3.4s');
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

  it('marks the gutter only where the minute moved, so the column is a timeline', () => {
    // Stamped against the real clock, because `Transcript` reads `useNow` itself
    // and `clockTime` prints a bare date once a moment is a week old — which
    // would collapse all three marks to one day and hide the minute rule.
    //
    // Floored to a minute boundary first: from an arbitrary instant, a +44s
    // offset would cross into the next minute for most of every minute, and the
    // test would fail on the clock rather than on the rule.
    const base = Math.floor((Date.now() - 5 * 60_000) / 60_000) * 60_000;
    const at = (offsetMs: number) => new Date(base + offsetMs).toISOString();
    render(
      <Transcript
        truncatedBefore={false}
        items={[said('m1', at(4_000)), said('m2', at(48_000)), said('m3', at(62_000))]}
      />,
    );
    const printed = screen
      .getAllByTestId('transcript-card')
      .map((c) => within(c).queryByTestId('item-time')?.textContent ?? '');
    // Two marks for three items: the second says nothing new.
    expect(printed[0]).not.toBe('');
    expect(printed[1]).toBe('');
    expect(printed[2]).not.toBe('');
    expect(printed[2]).not.toBe(printed[0]);
  });

  it('leaves the gutter empty for an item that carries no time', () => {
    render(
      <Transcript
        truncatedBefore={false}
        items={[{ id: 'm1', ts: '', type: 'message', role: 'assistant', markdown: 'hi' }]}
      />,
    );
    expect(screen.queryByTestId('item-time')).toBeNull();
  });

  it('carries the machine-readable instant and the full time on the mark', () => {
    render(
      <Transcript
        truncatedBefore={false}
        items={[
          {
            id: 'm1',
            ts: '2026-09-05T07:31:04Z',
            type: 'message',
            role: 'assistant',
            markdown: 'hi',
          },
        ]}
      />,
    );
    const time = screen.getByTestId('item-time');
    expect(time.tagName).toBe('TIME');
    expect(time).toHaveAttribute('datetime', '2026-09-05T07:31:04Z');
  });

  it('the item that draws nothing takes no mark and does not move the run', () => {
    const skipped: TranscriptItem = {
      id: 'w1',
      // An hour later, so a mark here would be unmistakable.
      ts: '2026-09-05T08:31:00Z',
      type: 'tool_call',
      toolUseId: 'w1',
      tool: 'AskUserQuestion',
      input: {},
      status: 'running',
    };
    expect(raisesAWait(skipped)).toBe(true);
    render(
      <Transcript
        truncatedBefore={false}
        items={[said('m1', '2026-09-05T07:31:04Z'), skipped, said('m2', '2026-09-05T07:31:48Z')]}
      />,
    );
    // The skipped item's hour never appears, and the message after it still
    // reads as the same minute as the one before.
    expect(screen.getAllByTestId('item-time')).toHaveLength(1);
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

  it('a shell command renders as a command and its output', () => {
    render(
      <Transcript
        truncatedBefore={false}
        items={[
          {
            id: 'sh1',
            ts: '',
            type: 'shell',
            command: 'git status',
            output: 'on main',
            status: 'done',
          },
        ]}
      />,
    );
    const card = screen.getByTestId('transcript-card');
    expect(card).toHaveTextContent('git status');
    expect(card).toHaveTextContent('on main');
  });

  it('a tagged message renders as a message, with the tag stripped out of it', () => {
    const items = goldenItems('document-content.jsonl');
    render(<Transcript items={items} truncatedBefore={false} />);
    const card = screen
      .getAllByTestId('transcript-card')
      .find((c) => c.getAttribute('data-item-type') === 'message')!;
    expect(within(card).getByText(/Here is the changelog you asked for/)).toBeInTheDocument();
    expect(card.textContent).not.toContain('doc-content');
    expect(card.textContent).not.toContain('1.4.0');
  });

  it('an image an agent showed renders as a picture in its message', () => {
    const items = goldenItems('image-worktree.jsonl');
    render(<Transcript items={items} truncatedBefore={false} />);
    const card = screen
      .getAllByTestId('transcript-card')
      .find((c) => c.getAttribute('data-item-type') === 'message')!;
    // The exact ref the Python golden recorded. A substring match would pass
    // through a change to the id, so it would not show the two agreeing.
    expect(within(card).getByAltText('The failing dialog')).toHaveAttribute(
      'src',
      '/api/files/ag1-2-shot.png',
    );
    // The prose either side of the picture is kept, and no tag syntax shows.
    expect(within(card).getByText(/Here is the failing dialog/)).toBeInTheDocument();
    expect(card.textContent).not.toContain('<image');
  });

  it('an image the agent may not show says so instead of breaking', () => {
    const items = goldenItems('image-escaping.jsonl');
    render(<Transcript items={items} truncatedBefore={false} />);
    const card = screen
      .getAllByTestId('transcript-card')
      .find((c) => c.getAttribute('data-item-type') === 'message')!;
    // No thumbnail button: the refused image reaches none of the lightbox path.
    expect(within(card).queryByRole('button')).not.toBeInTheDocument();
    expect(card.textContent).toContain('could not be shown');
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
