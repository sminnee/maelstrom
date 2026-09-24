import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { Transcript } from './Transcript';
import { classifyToolCall } from './toolCards';
import { makePlanReview } from '../test/fixtures';
import { goldenItems } from '../test/goldens';
import type { TranscriptItem } from '../protocol/transcript';

const raisesAWait = (item: TranscriptItem) =>
  item.type === 'tool_call' && classifyToolCall(item) === 'wait';

/** One message. The id doubles as the body, so a row names itself. */
const said = (
  id: string,
  ts: string,
  { role = 'assistant', markdown = id }: { role?: 'user' | 'assistant'; markdown?: string } = {},
): TranscriptItem => ({
  id,
  ts,
  type: 'message',
  role,
  markdown,
});

describe('Transcript', () => {
  it('shows an unsupported Codex event as expandable JSON', () => {
    const item: TranscriptItem = {
      id: 'item-raw' as TranscriptItem['id'],
      ts: '2026-09-01T00:00:00Z',
      type: 'raw_event',
      method: 'item/started',
      params: { threadId: 'thread-1', item: { type: 'agentMessage' } },
    };

    render(<Transcript items={[item]} truncatedBefore={false} />);

    expect(screen.getByTestId('raw-event')).toHaveTextContent(
      'Codex · item/started{ "threadId": "thread-1"',
    );
  });

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

  it('registers a tool call as machinery and a plain message as prose, in item order', () => {
    // This rule has no test today, and a shell item once drew one register
    // while the CSS spaced it as the other — exactly what keying both off one
    // computed value, read as `data-register`, is meant to prevent.
    render(
      <Transcript
        truncatedBefore={false}
        items={[
          said('m1', '', { markdown: 'Free port 342 before you retry.' }),
          {
            id: 't1',
            ts: '',
            type: 'tool_call',
            toolUseId: 't1',
            tool: 'Bash',
            input: { command: 'mael env list' },
            status: 'done',
          },
          said('m2', '', { markdown: 'Done.' }),
          {
            id: 'ms1',
            ts: '',
            type: 'milestone',
            name: 'built',
            recognised: true,
            deltaTokens: 95_000,
            costDelta: 2.1,
          },
        ]}
      />,
    );
    const registers = screen
      .getAllByTestId('transcript-card')
      .map((c) => c.getAttribute('data-register'));
    expect(registers).toEqual(['prose', 'machinery', 'prose', 'machinery']);
  });

  it('an all-low agent message registers as machinery; a mixed one registers as prose', () => {
    render(
      <Transcript
        truncatedBefore={false}
        items={[
          said('m1', '', { markdown: '<user-attention low>\nChecking the allocator first.' }),
          said('m2', '', {
            markdown: 'Free port 342.\n\n<user-attention low>\nChecking the allocator first.',
          }),
        ]}
      />,
    );
    const cards = screen.getAllByTestId('transcript-card');
    expect(cards[0]).toHaveAttribute('data-register', 'machinery');
    expect(cards[1]).toHaveAttribute('data-register', 'prose');
  });

  it('draws no visible role label, agent or you, on any message', () => {
    render(
      <Transcript
        truncatedBefore={false}
        items={[
          said('m1', '', { role: 'user', markdown: 'Free it.' }),
          said('m2', '', { markdown: 'Done.' }),
        ]}
      />,
    );
    // The deleted label rendered these exact lowercase strings as their own
    // visible text node; the uppercase look came from CSS alone, which
    // `textContent` never sees, so the check must match the removed markup,
    // not its paint. `ignore: '.srOnly'` excludes the user turn's intended,
    // screen-reader-only "you" — that one is meant to survive.
    for (const word of ['you', 'agent']) {
      expect(screen.queryByText(word, { exact: true, ignore: '.srOnly' })).toBeNull();
    }
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

  it('a task notification keeps its summary and drops the plumbing around it', () => {
    // The reported shape, from the golden: the turn also carried a task id, a
    // tool-use id and a path on the agent's host. None of them address
    // anything the reader can open from here, so the line must not print them.
    const items = goldenItems('string-content-turn.jsonl');
    render(<Transcript items={items} truncatedBefore={false} />);
    const [, background] = screen.getAllByTestId('task-notification');
    expect(background).toHaveTextContent(
      'Background command "Run the Python gate after doc edits" completed (exit code 0)',
    );
    for (const address of ['b7ypbbna6', 'toolu_', '/private/tmp']) {
      expect(background).not.toHaveTextContent(address);
    }
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

  it('an agent message marks quiet self-talk apart from the prose around it', () => {
    // CSS is invisible to vitest, so the rank is asserted as structure: the
    // Quiet self-talk is its own element, and the prose either side stays in
    // the message rather than being swallowed by it.
    render(
      <Transcript
        truncatedBefore={false}
        items={[
          said('m1', '', {
            markdown:
              'Free port 342 before you retry.\n\n<user-attention low>\nChecking the allocator first.\n\n<user-attention high>\nThen I will re-run the suite.',
          }),
        ]}
      />,
    );
    const card = screen.getByTestId('transcript-card');
    const quiet = within(card).getByTestId('quiet');
    expect(quiet).toHaveTextContent('Checking the allocator first.');
    expect(within(card).getByText(/Free port 342 before you retry/)).toBeInTheDocument();
    expect(within(card).getByText(/Then I will re-run the suite/)).toBeInTheDocument();
    // The surrounding prose is not part of the quiet self-talk.
    expect(quiet).not.toHaveTextContent('Free port 342 before you retry');
  });

  it('an untagged agent message still renders its prose', () => {
    render(
      <Transcript
        truncatedBefore={false}
        items={[said('m1', '', { markdown: 'No quiet block here, just the working commentary.' })]}
      />,
    );
    const card = screen.getByTestId('transcript-card');
    expect(within(card).queryByTestId('quiet')).toBeNull();
    expect(within(card).getByText(/just the working commentary/)).toBeInTheDocument();
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

  it('draws only the window it was given, and offers the rest behind a button', () => {
    const items = Array.from({ length: 120 }, (_, i) => said(`m${i}`, '2026-09-05T07:31:04Z'));
    render(
      <Transcript
        items={items.slice(70)}
        truncatedBefore={false}
        hiddenCount={70}
        revealSize={50}
      />,
    );
    expect(screen.getAllByTestId('transcript-card')).toHaveLength(50);
    // 70 are held back, but a click reveals one window of them. The button
    // names the act, not the remainder.
    expect(screen.getByRole('button', { name: 'Show 50 earlier events' })).toBeInTheDocument();
  });

  it('offers only what is left when fewer than a window remain above', () => {
    const items = Array.from({ length: 30 }, (_, i) => said(`m${i}`, '2026-09-05T07:31:04Z'));
    render(
      <Transcript
        items={items.slice(20)}
        truncatedBefore={false}
        hiddenCount={20}
        revealSize={50}
      />,
    );
    // The last click opens the rest, so here the two figures agree.
    expect(screen.getByRole('button', { name: 'Show 20 earlier events' })).toBeInTheDocument();
  });

  it('offers nothing once the whole transcript is on screen', () => {
    const items = Array.from({ length: 3 }, (_, i) => said(`m${i}`, '2026-09-05T07:31:04Z'));
    render(<Transcript items={items} truncatedBefore={false} hiddenCount={0} />);
    expect(screen.queryByRole('button', { name: /earlier events/ })).toBeNull();
  });

  it('keeps the two notes distinct, because they say different things', () => {
    // `truncatedBefore` means events that exist nowhere; the button means events
    // that exist and are one click away. Both can be true at once.
    const items = Array.from({ length: 60 }, (_, i) => said(`m${i}`, '2026-09-05T07:31:04Z'));
    render(<Transcript items={items.slice(10)} truncatedBefore hiddenCount={10} />);
    const note = screen.getByText('Earlier events were not kept.');
    const button = screen.getByRole('button', { name: 'Show 10 earlier events' });
    expect(note).toBeInTheDocument();
    // The lost events are older than the ones a click would reveal, so the note
    // reads above the button. `compareDocumentPosition` is tree order, which
    // jsdom does implement, where layout order is beyond it.
    expect(note.compareDocumentPosition(button)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it('asks for more when the button is clicked', () => {
    const onShowMore = vi.fn();
    const items = Array.from({ length: 60 }, (_, i) => said(`m${i}`, '2026-09-05T07:31:04Z'));
    render(
      <Transcript
        items={items.slice(10)}
        truncatedBefore={false}
        hiddenCount={10}
        onShowMore={onShowMore}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Show 10 earlier events' }));
    expect(onShowMore).toHaveBeenCalled();
  });

  it('keeps drawing the rest of the transcript when one card throws', () => {
    // A transcript draws whatever an agent wrote. Without a boundary per card,
    // one bad item unmounts the whole app and the operator loses the board.
    const exploding = {
      id: 'bad',
      ts: '',
      type: 'message',
      role: 'assistant',
      // `markdown` is a string everywhere else; a non-string reaches
      // react-markdown and throws during render.
      markdown: { not: 'a string' },
    } as unknown as TranscriptItem;
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      render(
        <Transcript
          truncatedBefore={false}
          items={[
            said('m1', '', { markdown: 'before' }),
            exploding,
            said('m2', '', { markdown: 'after' }),
          ]}
        />,
      );
      expect(screen.getByTestId('card-error')).toBeInTheDocument();
      expect(screen.getByText('before')).toBeInTheDocument();
      expect(screen.getByText('after')).toBeInTheDocument();
    } finally {
      errors.mockRestore();
    }
  });

  it('a denied permission shows its decision', () => {
    render(<Transcript items={goldenItems('permission-denied.jsonl')} truncatedBefore={false} />);
    const card = screen
      .getAllByTestId('transcript-card')
      .find((c) => c.querySelector('[data-tool-kind="bash"]'))!;
    expect(within(card).getByText('denied')).toBeInTheDocument();
  });
});

describe('the milestone bar', () => {
  const reached = (name: string, recognised = true): TranscriptItem => ({
    id: `item-${name}` as TranscriptItem['id'],
    ts: '2026-09-01T00:00:00Z',
    type: 'milestone',
    name,
    recognised,
    deltaTokens: 95_000,
    costDelta: 2.1,
  });

  it('says which stage closed and what that stage cost', () => {
    // The delta, never the running total: "which stage was expensive" is the
    // reading the ledger exists to give.
    render(<Transcript items={[reached('built')]} truncatedBefore={false} />);
    expect(screen.getByTestId('milestone')).toHaveTextContent('built · 95k · $2.10');
  });

  it('flags a name the flow does not declare, and does not light the rule for it', () => {
    // Never dropped — a typo must be visible. But an undeclared name has
    // closed no stage anyone can price, so it does not take the lit rule.
    render(<Transcript items={[reached('deployed', false)]} truncatedBefore={false} />);
    const bar = screen.getByTestId('milestone');
    expect(bar).toHaveTextContent('deployed (?)');
    expect(bar).toHaveAttribute('data-recognised', 'false');
  });

  it('says only the stage when the stage spent nothing', () => {
    // A stage reached twice deltas to zero against the row before it, which
    // is a real ledger state. `· 0k · $0.00` would read as a measurement.
    const item = { ...reached('built'), deltaTokens: 0, costDelta: 0 };
    render(<Transcript items={[item]} truncatedBefore={false} />);
    expect(screen.getByTestId('milestone')).toHaveTextContent(/^built$/);
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
