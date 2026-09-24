import { describe, expect, it } from 'vitest';
import { MODES, nextMode } from './modes';

describe('the permission mode cycle', () => {
  it('visits every mode and comes back round', () => {
    expect(MODES.reduce<string>((mode) => nextMode(mode), 'plan')).toBe('plan');
  });

  it('starts the cycle over from a mode it does not know', () => {
    expect(nextMode('')).toBe('plan');
  });

  it('mirrors the order agent_model.py cycles in', () => {
    expect(nextMode('plan')).toBe('auto');
    expect(nextMode('auto')).toBe('normal');
    expect(nextMode('normal')).toBe('plan');
  });
});
