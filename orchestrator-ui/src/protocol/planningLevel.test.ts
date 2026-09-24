import { describe, expect, it } from 'vitest';
import { PLANNING_LEVELS, fieldsForLevel, levelForFields } from './planningLevel';

describe('the planning level', () => {
  it('writes the command and mode pair each level stands for', () => {
    // The table in docs/dev/orchestrator-ui.md, "Starting new work". High and
    // None mirror `mode_for_command` in task.py; Regular has no equivalent
    // there, which is why the level is a UI-side reading and not a wire field.
    expect(fieldsForLevel('high')).toEqual({ command: 'plan-task', mode: 'normal' });
    expect(fieldsForLevel('regular')).toEqual({ command: '', mode: 'plan' });
    expect(fieldsForLevel('none')).toEqual({ command: '', mode: 'auto' });
  });

  it('reads a level back off the pair it wrote, for every level', () => {
    // The round trip is what lets the radio and the two fields be one value
    // read two ways rather than two values kept in sync.
    for (const level of PLANNING_LEVELS) {
      expect(levelForFields(fieldsForLevel(level))).toBe(level);
    }
  });

  it('reads no level from a pair no level stands for', () => {
    // An execute task under `normal`: legal in the notebook, and off this map.
    // The dialog shows N/A for it rather than silently moving the fields.
    expect(levelForFields({ command: '', mode: 'normal' })).toBeNull();
    // The right mode for a command the map does not hold.
    expect(levelForFields({ command: 'watch-pr', mode: 'normal' })).toBeNull();
    // The right command under the wrong mode.
    expect(levelForFields({ command: 'plan-task', mode: 'auto' })).toBeNull();
  });
});
