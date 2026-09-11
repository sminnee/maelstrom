import { describe, expect, it } from 'vitest';

import { modelLabel } from './models';

describe('modelLabel', () => {
  it('reads a resolved id as the alias it was launched with', () => {
    expect(modelLabel('claude-opus-5')).toBe('opus');
    expect(modelLabel('claude-fable-5-1')).toBe('fable');
  });

  it('leaves an alias alone', () => {
    expect(modelLabel('opus')).toBe('opus');
  });

  it('passes an unrecognised id through whole, rather than mangling it', () => {
    expect(modelLabel('claude-haiku-4-5-20251001')).toBe('claude-haiku-4-5-20251001');
  });

  it('needs a boundary after the alias, so a longer family name stays whole', () => {
    expect(modelLabel('claude-opusine-9')).toBe('claude-opusine-9');
  });

  it('drops an empty field, which both call sites filter out', () => {
    expect(modelLabel('')).toBe('');
  });
});
