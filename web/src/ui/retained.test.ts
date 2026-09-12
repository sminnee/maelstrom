import { describe, expect, it } from 'vitest';
import { isStaleRetainedKey, retainedKey } from './retained';

describe('the held-text keys', () => {
  // Literals, not the expressions the module builds them from: a test that
  // recomputed the key would agree with any typo the module made.
  it('names the new-work dialog with one versioned constant', () => {
    expect(retainedKey.newWork()).toBe('mael.retained.v1.new-work');
  });

  it('keys a message input per agent, so two agents never share held text', () => {
    expect(retainedKey.message('d9a4c7f1')).toBe('mael.retained.v1.message.d9a4c7f1');
    expect(retainedKey.message('d9a4c7f1.1')).toBe('mael.retained.v1.message.d9a4c7f1.1');
    expect(retainedKey.message('a')).not.toBe(retainedKey.message('b'));
  });

  it('reads a key of another version as stale, and a current one as live', () => {
    // What the lazy sweep rests on: bumping the version must make every older
    // key collectable without a migration, and must spare the live ones.
    expect(isStaleRetainedKey('mael.retained.v0.new-work')).toBe(true);
    expect(isStaleRetainedKey(retainedKey.newWork())).toBe(false);
    expect(isStaleRetainedKey(retainedKey.message('d9a4c7f1'))).toBe(false);
    // Nothing else in storage is ours to remove.
    expect(isStaleRetainedKey('some.other.app.key')).toBe(false);
  });
});
