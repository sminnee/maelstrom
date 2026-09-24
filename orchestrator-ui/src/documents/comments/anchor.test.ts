import { describe, expect, it } from 'vitest';
import { buildAnchor, locateQuote } from './anchor';

const source = '# Plan\n\nAdd the export. Cap it at 10,000 rows.\n\nAdd the export again later.\n';

describe('buildAnchor', () => {
  it('takes the quote and up to 32 characters of context each side', () => {
    const start = source.indexOf('Cap it');
    const anchor = buildAnchor(source, start, start + 'Cap it'.length);
    expect(anchor).toEqual({
      quote: 'Cap it',
      prefix: '# Plan\n\nAdd the export. ',
      suffix: ' at 10,000 rows.\n\nAdd the export',
      start,
      end: start + 6,
    });
  });
});

describe('locateQuote', () => {
  it('finds the quote whose context matches when the quote repeats', () => {
    const anchor = {
      quote: 'Add the export',
      prefix: 'rows.\n\n',
      suffix: ' again',
      start: 0,
      end: 0,
    };
    const hit = locateQuote(source, anchor);
    expect(hit).toEqual({
      start: source.lastIndexOf('Add the export'),
      end: source.lastIndexOf('Add the export') + 14,
    });
  });

  it('falls back to the first quote match when the context does not match', () => {
    const anchor = {
      quote: 'Add the export',
      prefix: 'nothing like this',
      suffix: 'nor this',
      start: 0,
      end: 0,
    };
    expect(locateQuote(source, anchor)).toEqual({
      start: source.indexOf('Add the export'),
      end: source.indexOf('Add the export') + 14,
    });
  });

  it('is null when the quote is not in the source', () => {
    expect(
      locateQuote(source, { quote: 'Remove it', prefix: '', suffix: '', start: 0, end: 0 }),
    ).toBeNull();
  });

  it('prefers the occurrence whose context matches over stale cached offsets', () => {
    const first = source.indexOf('Add the export');
    const anchor = {
      quote: 'Add the export',
      prefix: 'rows.\n\n',
      suffix: ' again',
      start: first,
      end: first + 14,
    };
    expect(locateQuote(source, anchor)).toEqual({
      start: source.lastIndexOf('Add the export'),
      end: source.lastIndexOf('Add the export') + 14,
    });
  });

  it('trusts cached offsets only when they still hold the quote', () => {
    const start = source.indexOf('Cap it');
    expect(
      locateQuote(source, { quote: 'Cap it', prefix: '', suffix: '', start, end: start + 6 }),
    ).toEqual({ start, end: start + 6 });
    expect(
      locateQuote(source, { quote: 'Cap it', prefix: '', suffix: '', start: 0, end: 6 }),
    ).toEqual({ start, end: start + 6 });
  });
});
