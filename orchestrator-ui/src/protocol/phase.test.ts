import { describe, expect, it } from 'vitest';
import { isActionable, KNOWN_COMMANDS, phaseForCommand } from './phase';
import { makeTask } from '../test/fixtures';

describe('phaseForCommand', () => {
  it.each([
    ['shape', 'shape'],
    ['plan-task', 'plan'],
    ['plan-next-step', 'plan'],
    ['watch-pr', 'land'],
    ['impeccable shape', 'shape'],
    ['impeccable critique', 'shape'],
    ['impeccable audit', 'shape'],
    ['impeccable polish', 'build'],
    ['impeccable animate', 'build'],
    ['impeccable optimize', 'build'],
    // Suggested by the editor, so its phase is pinned, not just its presence.
    ['impeccable layout', 'build'],
    // An execute task runs no skill, so an empty command is the ordinary build case.
    ['', 'build'],
  ])('%j → %s', (command, phase) => {
    expect(phaseForCommand(command)).toBe(phase);
  });

  it.each([
    'some-other-skill',
    'watch-prs',
    'comand',
    'impeccable',
    'impeccable nonsense',
    'impeccable init',
    'impeccable live',
  ])('%j has no phase', (command) => {
    expect(phaseForCommand(command)).toBeNull();
  });

  it('gives every suggested command a phase', () => {
    // Asserted, or an emptied shortlist would pass on an empty loop.
    expect(KNOWN_COMMANDS.length).toBeGreaterThan(0);
    for (const command of KNOWN_COMMANDS) {
      expect(phaseForCommand(command), command).not.toBeNull();
    }
  });
});

describe('isActionable', () => {
  const done = makeTask({ id: 'A', status: 'done' });
  const open = makeTask({ id: 'B', status: 'in-progress' });

  it('is true for a todo task whose follows are all done', () => {
    const task = makeTask({ id: 'C', status: 'todo', follows: ['A'] });
    expect(isActionable(task, { A: done, B: open, C: task })).toBe(true);
  });

  it('is false while a followed task is not done', () => {
    const task = makeTask({ id: 'C', status: 'todo', follows: ['A', 'B'] });
    expect(isActionable(task, { A: done, B: open, C: task })).toBe(false);
  });

  it.each(['done', 'cancelled', 'blocked', 'template'] as const)('is false when %s', (status) => {
    const task = makeTask({ id: 'C', status });
    expect(isActionable(task, { C: task })).toBe(false);
  });
});
