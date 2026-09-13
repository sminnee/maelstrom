import { describe, expect, it } from 'vitest';
import { branchFromDraft, titleFromDraft } from './branchFromDraft';

// Every expected value here came from running `_first_line` and `slugify` in
// `src/maelstrom/branch_name.py`, not from reading them.
describe('naming a task from its prose alone', () => {
  it('takes the title from the draft first line', () => {
    expect(titleFromDraft('The export drops a row\n\nIt happens on the last page.')).toBe(
      'The export drops a row',
    );
  });

  it('skips the blank lines a draft opens with, as `_first_line` does', () => {
    // An Enter pressed before typing, or a paste from another document. The
    // literal first line is `''`, which would write a task with no title.
    expect(titleFromDraft('\nThe export drops a row')).toBe('The export drops a row');
    expect(branchFromDraft('\nThe export drops a row')).toBe('feat/export-drops-row');
    expect(titleFromDraft('   \n  Fix the header')).toBe('Fix the header');
    expect(branchFromDraft('\n\n\nDeep')).toBe('feat/deep');
  });

  it('caps the title where inference caps it, trimming what the cap exposes', () => {
    // The cap lands inside the two spaces, and `_first_line` ends on `rstrip`,
    // so the trailing space goes rather than riding along.
    expect(titleFromDraft(`${'A'.repeat(78)}  tail`)).toBe('A'.repeat(78));
    expect(titleFromDraft('x'.repeat(100))).toBe('x'.repeat(80));
  });

  it('slugs the first four meaningful words, dropping the stopwords Python drops', () => {
    // "the" and "a" are in `_STOPWORDS`, so the kept words carry the meaning.
    expect(branchFromDraft('The export drops a row on the last page')).toBe(
      'feat/export-drops-row-last',
    );
  });

  it('drops punctuation rather than slugging it', () => {
    expect(branchFromDraft("Fix the CSV export's header!")).toBe('feat/fix-csv-export-s');
  });

  it('keeps the raw words when every one is a stopword', () => {
    // Python falls back to the unstripped words rather than yielding nothing.
    // Every value here came from running `slugify` and `generate_branch_name`
    // rather than from reading them.
    expect(branchFromDraft('the a an of to')).toBe('feat/the-a-an-of');
    // Not the same case: `not` is no stopword, so one word survives the strip
    // and the fallback above never fires.
    expect(branchFromDraft('to be or not to be')).toBe('feat/not');
  });

  it('names something for prose that slugs to nothing at all', () => {
    expect(branchFromDraft('!!!')).toBe('feat/task');
    expect(branchFromDraft('')).toBe('feat/task');
  });
});
