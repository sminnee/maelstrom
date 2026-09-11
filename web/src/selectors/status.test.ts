import { describe, expect, it } from 'vitest';
import { describeDocumentStatus, describePrState, prStateKey, prTone } from './status';

describe('describeDocumentStatus', () => {
  it('says a hyphenated status in words', () => {
    expect(describeDocumentStatus('awaiting-review')).toBe('awaiting review');
  });
});

describe('prStateKey', () => {
  it('reads a draft as a draft, whatever its checks are doing', () => {
    expect(prStateKey('ci-failed', true)).toBe('draft');
    expect(prStateKey('merged', true)).toBe('draft');
  });

  it('reads any other PR as its own state', () => {
    expect(prStateKey('ci-running', false)).toBe('ci-running');
  });

  it('reads no PR as no state', () => {
    expect(prStateKey('', false)).toBe('');
  });
});

describe('describePrState', () => {
  it('says each state in words', () => {
    expect(describePrState('merged', false)).toBe('merged');
    expect(describePrState('ci-failed', false)).toBe('CI failed');
    expect(describePrState('ci-running', false)).toBe('CI running');
    expect(describePrState('conflict', false)).toBe('merge conflicts');
    expect(describePrState('unknown', false)).toBe('checking');
    expect(describePrState('checks-unreadable', false)).toBe('checks not readable');
    expect(describePrState('ready', false)).toBe('ready to merge');
    expect(describePrState('ci-failed', true)).toBe('draft');
  });

  it('parts checks it cannot read from checks still running', () => {
    // `checking` promises an answer is coming. When the token may not read the
    // checks at all, none is, so saying so is the only honest reading.
    expect(describePrState('checks-unreadable', false)).not.toBe(describePrState('unknown', false));
  });

  it('draws nothing for no PR', () => {
    expect(describePrState('', false)).toBe('');
  });

  it('draws nothing for a state the wire invented', () => {
    // `prState` crosses as a plain string from Python, so a value outside the
    // union must draw nothing rather than render "PR #278 · undefined".
    expect(describePrState('sideways' as never, false)).toBe('');
  });
});

describe('prTone', () => {
  it('parts merged from ready: settled is not the same as go-merge-it', () => {
    // The old dot gave both `--ok`, so the board could not tell "nothing left
    // to do" from "your turn".
    expect(prTone('merged', false)).not.toBe(prTone('ready', false));
  });

  it('puts a failed build and a conflict in the same demand on the operator', () => {
    expect(prTone('ci-failed', false)).toBe('bad');
    expect(prTone('conflict', false)).toBe('bad');
  });

  it('gives every state a tone', () => {
    const states = [
      'merged',
      'ci-failed',
      'ci-running',
      'conflict',
      'checks-unreadable',
      'unknown',
      'ready',
    ] as const;
    for (const state of states) expect(prTone(state, false)).toBeTruthy();
    expect(prTone('ci-failed', true)).toBe('quiet');
  });

  it('does not colour a reading it cannot vouch for', () => {
    // The same rule the usage chips follow: a value nothing stands behind
    // gives up its tone rather than shouting in a colour it has not earned.
    expect(prTone('checks-unreadable', false)).toBe('quiet');
  });

  it('reads a state the wire invented as no state at all', () => {
    expect(prTone('sideways' as never, false)).toBe('neutral');
    expect(prTone('', false)).toBe('neutral');
  });
});
